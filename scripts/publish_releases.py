#!/usr/bin/env python3
"""Publish version tags and merge channel/platform coverage into one Release."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import urllib.parse

from chrome_archive import get_release, gh, query_omaha, sha256
from release_metadata import (CHANNELS, VERSION, existing_release_manifests,
                              google_https, manifest_asset_name, metadata_comment, validate_manifest)
from archive_logging import log_context, log_event, log_runtime, stage
from linux_sources import rpm_sources
from updater_state import merge_discovery, read_state, write_state


def release_text(version, manifests):
    # Keep every existing attachment's provenance even when a channel gains a shared alias.
    records = {(item['channel'], item['platform'], item['architecture'], manifest_asset_name(item)): item
               for item in manifests}
    channels = [CHANNELS[channel] for channel in CHANNELS if any(row[0] == channel for row in records)]
    title = version + ' · ' + ' / '.join(channels)
    body = f'# Chrome {version}\n\n'
    body += 'Official Google download payloads, archived without executing an installer.\n\n'
    body += '| Channel | Platform | Architecture | Official original download |\n|---|---|---|---|\n'
    legacy_sources = False
    downloads = {}
    for (channel, platform, arch, _), item in sorted(records.items()):
        source = item.get('source', {})
        url = source.get('url') if platform == 'linux' else source.get('native_url') or source.get('url')
        suffix = urllib.parse.urlsplit(url).path.lower().rsplit('.', 1)[-1] if url else ''
        label = {'exe': 'EXE', 'zip': 'ZIP', 'deb': 'DEB', 'crx3': 'CRX3'}.get(suffix, 'Google original')
        legacy_sources |= label == 'CRX3'
        link = f'[{label}]({google_https(url)})' if url else '—'
        if platform == 'linux' and source.get('rpm_url'):
            link += f' · [RPM]({google_https(source["rpm_url"])})'
        downloads.setdefault((platform, arch, link), set()).add(channel)
    for (platform, arch, link), coverage in sorted(downloads.items()):
        names = ' / '.join(CHANNELS[channel] for channel in CHANNELS if channel in coverage)
        body += f'| {names} | {dict(windows="Windows", macos="macOS", linux="Linux")[platform]} | {arch.upper()} | {link} |\n'
    body += '\n## Assets\n\n'
    body += '- `chrome-windows-*-browser.7z`: extracted Windows browser files, with `Chrome-bin/` as the archive root.\n'
    body += '- `chrome-macos-*-browser.tar.xz`: the complete `Google Chrome.app/` application bundle, preserving file contents, Unix modes and symbolic links. Extract the archive to obtain the `.app` directory.\n'
    body += '- `chrome-linux-*-browser.tar.xz`: extracted Linux browser files, with `chrome/` as the archive root, preserving Unix modes and symbolic links.\n'
    body += '- Official original downloads: EXE is the Windows uncompressed installer; ZIP is the macOS updater package containing the app bundle; DEB is the Linux archive input, with a matching-version RPM link when available.\n'
    body += '- EXE/ZIP links point directly to official packages for the archived version. MSI, DMG, PKG and Enterprise bundles are not enumerated here.\n'
    if legacy_sources:
        body += '- Legacy CRX3 links are retained where no matching EXE/ZIP source was recovered for an older archive.\n'
    body += '- New asset names identify platform, architecture and version; identical archives are shared across channels. A SHA-256 suffix distinguishes different payloads for the same version. Legacy names may include a channel.\n'
    body += '- Only the repacked browser archives are release attachments; original files and workflow metadata are not uploaded as release assets.\n\n'
    body += 'Linux has no official Extended Stable channel. Extracted archives do not install dependencies or updater integration.\n'
    if any('browser_asset' in item or item.get('source', {}).get('sha256') for item in records.values()):
        body += '\n' + metadata_comment([records[key] for key in sorted(records)])
    return title, body


def add_linux_rpm_links(manifests):
    known = {(item['architecture'], item['version']): item['source']['rpm_url']
             for item in manifests if item['platform'] == 'linux' and item.get('source', {}).get('rpm_url')}
    result = []
    for item in manifests:
        if item['platform'] == 'linux' and not item.get('source', {}).get('rpm_url'):
            with stage('query_matching_rpm', version=item['version'], architecture=item['architecture']):
                url = known.get((item['architecture'], item['version'])) or rpm_sources(item['architecture']).get(item['version'])
            if url:
                item = dict(item, source={**item.get('source', {}), 'rpm_url': url})
            else:
                log_event('matching_rpm_unavailable', version=item['version'], architecture=item['architecture'])
        result.append(item)
    return result


def add_native_source_links(manifests):
    """Recover native links for legacy records; native inputs need no requery."""
    known = {(item['platform'], item['architecture'], item['channel'], item['version']): item['source']['native_url']
             for item in manifests if item.get('source', {}).get('native_url')}
    queried = {}
    result = []
    for item in manifests:
        source = item.get('source', {})
        suffix = '.exe' if item['platform'] == 'windows' else '.zip'
        native_input = urllib.parse.urlsplit(source.get('url', '')).path.lower().endswith(suffix)
        if item['platform'] == 'linux' or source.get('native_url') or native_input:
            result.append(item)
            continue
        key = (item['platform'], item['architecture'], item['channel'], item['version'])
        url = known.get(key)
        if not url:
            target = key[:3]
            if target not in queried:
                with stage('query_native_source', platform=target[0], architecture=target[1], channel=target[2]):
                    queried[target] = query_omaha(*target, allow_no_update=True)
            offer = queried[target]
            if offer is None or offer.version != item['version']:
                pinned = (*target, item['version'])
                if pinned not in queried:
                    with stage('query_native_source_version', platform=target[0], architecture=target[1],
                               channel=target[2], version=item['version']):
                        queried[pinned] = query_omaha(*target, allow_no_update=True, target_version=item['version'])
                offer = queried[pinned]
            if offer is not None and offer.version == item['version']:
                expected = '_chrome_installer_uncompressed.exe' if item['platform'] == 'windows' else '.zip'
                if not offer.package_name.endswith(expected):
                    raise ValueError(f'Unexpected standalone updater package: {offer.package_name}')
                url = offer.urls[0]
                log_event('native_source_found', platform=item['platform'], architecture=item['architecture'],
                          channel=item['channel'], version=item['version'], url=url,
                          package=offer.package_name, size=offer.size, sha256=offer.digest)
            else:
                log_event('matching_native_source_unavailable', platform=item['platform'], architecture=item['architecture'],
                          channel=item['channel'], version=item['version'], offered_version=offer.version if offer else None,
                          latest_offered_version=queried[target].version if queried[target] else None)
        if url:
            item = dict(item, source={**item.get('source', {}), 'native_url': google_https(url)})
        result.append(item)
    return result


def local_groups(root):
    groups = {}
    for path in root.glob('*/*-manifest.json'):
        item = validate_manifest(json.loads(path.read_text(encoding='utf-8')), path.parent.name)
        expected = manifest_asset_name(item)
        entries = [entry for entry in item['files'] if entry['name'] == expected]
        if len(entries) != 1:
            raise ValueError(f'Expected one repacked browser archive in {path}')
        entry = entries[0]
        file = path.parent / expected
        with log_context(version=item['version'], platform=item['platform'], architecture=item['architecture'], channel=item['channel']):
            with stage('verify_release_archive', file=file, input_bytes=entry['size']):
                if not file.is_file() or file.stat().st_size != entry['size'] or sha256(file) != entry['sha256']:
                    raise ValueError(f'Invalid or missing release asset: {file}')
        group = groups.setdefault(item['version'], {'manifests': [], 'assets': {}})
        group['manifests'].append(item)
        group['assets'][file.name] = file
    for path in root.glob('*-discovery.json'):
        discovery = json.loads(path.read_text(encoding='utf-8'))
        for item in discovery.get('manifests', []):
            validate_manifest(item, item['version'])
            if (item['platform'] != discovery['platform'] or item['architecture'] != discovery['architecture']
                    or discovery['channels'].get(item['channel']) != item['version']):
                raise ValueError(f'Reused archive does not match discovery: {path}')
            group = groups.setdefault(item['version'], {'manifests': [], 'assets': {}})
            group['manifests'].append(item)
    return groups


def deduplicate_assets(group, existing_manifests, release):
    """Share only archives with matching size/digest within one platform/arch."""
    remote = {asset['name']: asset for asset in release['assets']} if release else {}
    candidates = {}
    for item in existing_manifests:
        asset = remote[manifest_asset_name(item)]
        if asset.get('digest') and asset.get('size'):
            candidates[(item['platform'], item['architecture'], asset['size'], asset['digest'])] = asset['name']
    uploads = {}
    mappings = {}
    for name, path in group['assets'].items():
        size, digest = path.stat().st_size, 'sha256:' + sha256(path)
        if name in remote:
            if remote[name].get('size') != size or remote[name].get('digest') != digest:
                raise ValueError(f'Existing immutable asset differs: {name}')
        items = [item for item in group['manifests'] if manifest_asset_name(item) == name]
        if not items:
            raise ValueError(f'Browser archive has no provenance: {name}')
        target = (items[0]['platform'], items[0]['architecture'], size, digest)
        selected = candidates.get(target, name)
        candidates[target] = selected
        mappings[name] = selected
        if selected == name:
            uploads[name] = path
    manifests = [dict(item, browser_asset=mappings.get(manifest_asset_name(item), manifest_asset_name(item)))
                 for item in group['manifests']]
    available = set(remote) | set(uploads)
    for item in manifests:
        if manifest_asset_name(item) not in available:
            raise ValueError(f'Reused browser archive is missing: {manifest_asset_name(item)}')
    return {'manifests': manifests, 'assets': uploads}


def discovered_versions(root):
    """Read the versions queried by this run, including already archived offers."""
    versions = set()
    for path in root.glob('*-discovery.json'):
        value = json.loads(path.read_text(encoding='utf-8'))
        channels = value.get('channels')
        if not isinstance(channels, dict) or not channels:
            raise ValueError(f'Invalid discovery channels: {path}')
        for channel, version in channels.items():
            if not isinstance(version, str):
                raise ValueError(f'Invalid discovery version: {path}')
            validate_manifest({'schema_version': value.get('schema_version'), 'version': version,
                               'platform': value.get('platform'), 'architecture': value.get('architecture'),
                               'channel': channel}, version)
            versions.add(version)
    log_event('refresh_versions_selected', versions=sorted(versions))
    return versions


def publish(repository, target, root):
    if not target:
        raise ValueError('A source commit is required for version tags')
    with stage('verify_release_inputs', root=root):
        groups = local_groups(root)
    for version, group in sorted(groups.items(), key=lambda pair: tuple(map(int, pair[0].split('.')))):
        with stage('read_existing_release', version=version):
            release = get_release(repository, version)
        existing_manifests = existing_release_manifests(release) if release else []
        group = deduplicate_assets(group, existing_manifests, release)
        manifests = add_native_source_links(add_linux_rpm_links([*existing_manifests, *group['manifests']]))
        title, body = release_text(version, manifests)
        if not release:
            release = gh('api', f'repos/{repository}/releases', '--method', 'POST', '--input', '-',
                         data={'tag_name': version, 'target_commitish': target, 'name': title,
                               'body': body,
                               'draft': True, 'prerelease': False})
        assets = {asset['name']: asset for asset in release['assets']}
        known = {manifest_asset_name(item) for item in [*existing_manifests, *group['manifests']]}
        unknown = {name for name in assets if name.endswith(('-browser.7z', '-browser.tar.xz'))} - known
        if unknown:
            raise ValueError(f'Existing browser archives have no provenance in the release description: {sorted(unknown)}')
        for name, path in group['assets'].items():
            if name in assets:
                # Never overwrite an existing version asset, including partial retries.
                asset = assets[name]
                expected = 'sha256:' + sha256(path)
                if asset.get('size') != path.stat().st_size or asset.get('digest') != expected:
                    raise ValueError(f'Existing immutable asset differs: {version}/{name}')
                continue
            with stage('upload_release_archive', version=version, file=path.name, input_bytes=path.stat().st_size):
                subprocess.run(['gh', 'release', 'upload', version, str(path), '--repo', repository], check=True)
        # Update name/coverage and publish only after all local assets uploaded.
        if (release.get('name') != title or release.get('body') != body
                or release.get('draft') or release.get('prerelease')):
            with stage('publish_release', version=version):
                gh('api', f'repos/{repository}/releases/{release["id"]}', '--method', 'PATCH', '--input', '-',
                   data={'name': title, 'body': body, 'draft': False, 'prerelease': False, 'make_latest': 'false'})
        log_event('release_published', version=version, title=title, uploaded_archive_count=len(group['assets']))
    if not groups:
        log_event('no_new_archives')
    return set(groups)


def refresh_release_descriptions(repository, versions):
    """Apply the current template only to versions offered in this run."""
    checked = updated = 0
    for version in sorted(set(versions)):
        if not VERSION.fullmatch(version):
            raise ValueError('Invalid refresh version')
        with stage('refresh_release_description', version=version):
            release = get_release(repository, version)
            if not release:
                log_event('release_description_skipped', version=version, reason='release_not_created')
                continue
            items = existing_release_manifests(release)
            names = {asset['name'] for asset in release['assets']
                     if asset['name'].endswith(('-browser.7z', '-browser.tar.xz'))}
            known = {manifest_asset_name(item) for item in items}
            if names - known:
                raise ValueError(f'Cannot recover source coverage for {version}: {sorted(names - known)}')
            if not items:
                log_event('release_description_skipped', version=version, reason='no_browser_archives')
                continue
            title, body = release_text(version, add_native_source_links(add_linux_rpm_links(items)))
            checked += 1
            if release['name'] != title or release.get('body') != body:
                gh('api', f'repos/{repository}/releases/{release["id"]}', '--method', 'PATCH', '--input', '-',
                   data={'name': title, 'body': body})
                updated += 1
    log_event('release_descriptions_refreshed', checked=checked, updated=updated)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('dist'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'), required=not bool(os.environ.get('GITHUB_REPOSITORY')))
    parser.add_argument('--target', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--preview', action='store_true', help='Print titles and coverage without writing to GitHub')
    parser.add_argument('--state', type=Path, help='Update persistent state only after publication succeeds')
    args = parser.parse_args()
    log_runtime(role='publish')
    if args.preview:
        for version, group in local_groups(args.input).items():
            title, body = release_text(version, add_native_source_links(add_linux_rpm_links(group['manifests'])))
            print(title + '\n\n' + body)
    else:
        targets = merge_discovery(read_state(args.state), args.input) if args.state else None
        versions = discovered_versions(args.input)
        versions.update(publish(args.repository, args.target, args.input))
        refresh_release_descriptions(args.repository, versions)
        if args.state:
            releases = {}
            for key, entry in targets.items():
                version = entry['version']
                if version == '0.0.0.0':
                    continue
                if version not in releases:
                    release = get_release(args.repository, version)
                    if not release or release.get('draft'):
                        raise ValueError(f'Updater state requires a published Release: {key}/{version}')
                    releases[version] = existing_release_manifests(release)
                platform, arch, channel = key.split('/')
                if not any(item['platform'] == platform and item['architecture'] == arch
                           and item['channel'] == channel and item['version'] == version
                           for item in releases[version]):
                    raise ValueError(f'Updater state requires an available browser archive: {key}/{version}')
            write_state(args.state, targets)


if __name__ == '__main__':
    main()
