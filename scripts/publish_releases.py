#!/usr/bin/env python3
"""Publish version tags and merge channel/platform coverage into one Release."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from chrome_archive import CHANNELS, VERSION, browser_asset_name, get_release, gh, google_https, query_omaha, sha256, validate_target
from archive_logging import log_context, log_event, log_runtime, stage
from linux_sources import rpm_sources


def validate_manifest(value, version):
    if value.get('schema_version') != 1 or value.get('version') != version or not VERSION.fullmatch(version):
        raise ValueError('Invalid archive manifest version')
    validate_target(value.get('platform'), value.get('architecture'))
    if value.get('channel') not in CHANNELS:
        raise ValueError('Invalid channel')
    if value['platform'] == 'linux' and value['channel'] != 'stable':
        raise ValueError('Linux Extended Stable is unsupported')
    return value


def release_text(version, manifests):
    entries = {(item['channel'], item['platform'], item['architecture']): item for item in manifests}
    rows = sorted(entries)
    channels = [CHANNELS[channel] for channel in CHANNELS if any(row[0] == channel for row in rows)]
    title = version + ' · ' + ' / '.join(channels)
    body = f'# Chrome {version}\n\n'
    body += 'Official Google download payloads, archived without executing an installer.\n\n'
    body += '| Channel | Platform | Architecture | Official original download |\n|---|---|---|---|\n'
    for channel, platform, arch in rows:
        source = entries[(channel, platform, arch)].get('source', {})
        url = source.get('url')
        label = 'DEB' if platform == 'linux' else 'CRX3'
        link = f'[{label}]({google_https(url)})' if url else '—'
        if platform == 'linux' and source.get('rpm_url'):
            link += f' · [RPM]({google_https(source["rpm_url"])})'
        if platform != 'linux' and source.get('native_url'):
            label = 'EXE' if platform == 'windows' else 'ZIP'
            link += f' · [{label}]({google_https(source["native_url"])})'
        body += f'| {CHANNELS[channel]} | {dict(windows="Windows", macos="macOS", linux="Linux")[platform]} | {arch.upper()} | {link} |\n'
    body += '\n## Assets\n\n'
    body += '- `*-browser.7z`: extracted Windows browser files.\n'
    body += '- `*-browser.tar.xz`: extracted macOS/Linux files, preserving Unix modes and symbolic links.\n'
    body += '- Official original files are available from the Google links in the table above. Windows/macOS CRX3 links identify the exact archive input; EXE (Windows uncompressed installer), ZIP (macOS updater package), and RPM links are included only when the official response matches the archived version and target. Linux browser files are extracted from DEB.\n'
    body += '- CRX3 contains the original Windows installer or macOS app bundle. A missing EXE/ZIP link means no matching alternate was recorded; it does not mean the CRX3 lacks those contents. MSI, DMG, PKG and Enterprise bundles are not enumerated here.\n'
    body += '- Asset prefixes identify platform, architecture and channel.\n'
    body += '- Only the repacked browser archives are release attachments; original files and workflow metadata are not uploaded as release assets.\n\n'
    body += 'Linux has no official Extended Stable channel. Extracted archives do not install dependencies or updater integration.\n'
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
    """Discover standalone updater packages, retaining already recorded links."""
    known = {(item['platform'], item['architecture'], item['channel'], item['version']): item['source']['native_url']
             for item in manifests if item.get('source', {}).get('native_url')}
    queried = {}
    result = []
    for item in manifests:
        if item['platform'] == 'linux' or item.get('source', {}).get('native_url'):
            result.append(item)
            continue
        key = (item['platform'], item['architecture'], item['channel'], item['version'])
        url = known.get(key)
        if not url:
            target = key[:3]
            if target not in queried:
                with stage('query_native_source', platform=target[0], architecture=target[1], channel=target[2]):
                    queried[target] = query_omaha(*target, accept_crx3=False)
            offer = queried[target]
            if offer is None or offer.version != item['version']:
                pinned = (*target, item['version'])
                if pinned not in queried:
                    with stage('query_native_source_version', platform=target[0], architecture=target[1],
                               channel=target[2], version=item['version']):
                        queried[pinned] = query_omaha(*target, accept_crx3=False, target_version=item['version'])
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
        expected = browser_asset_name(item['platform'], item['architecture'], item['channel'], item['version'])
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
    return groups


def existing_release_manifests(release):
    """The published table retains provenance; no metadata release assets needed."""
    pattern = re.compile(r'^\|\s*(Stable|Extended Stable)\s*\|\s*(Windows|macOS|Linux)\s*\|\s*(X86|X64|ARM64)\s*\|\s*\[(?:Google original|CRX3|DEB)\]\((https://[^\s)]+)\)(?:\s*·\s*\[(RPM|EXE|ZIP)\]\((https://[^\s)]+)\))?\s*\|\s*$', re.M)
    platforms = {'Windows': 'windows', 'macOS': 'macos', 'Linux': 'linux'}
    channels = {name: channel for channel, name in CHANNELS.items()}
    assets = {asset['name'] for asset in release['assets']}
    items = []
    for channel, platform, arch, url, extra_label, extra_url in pattern.findall(release.get('body') or ''):
        item = validate_manifest({'schema_version': 1, 'version': release['tag_name'],
                                  'channel': channels[channel], 'platform': platforms[platform],
                                  'architecture': arch.lower(), 'source': {'url': google_https(url)}}, release['tag_name'])
        if extra_url:
            if extra_label != {'Windows': 'EXE', 'macOS': 'ZIP', 'Linux': 'RPM'}[platform]:
                raise ValueError('Alternate source format does not match platform')
            item['source']['rpm_url' if platform == 'Linux' else 'native_url'] = google_https(extra_url)
        if browser_asset_name(item['platform'], item['architecture'], item['channel'], item['version']) in assets:
            items.append(item)
    return items


def publish(repository, target, root):
    if not target:
        raise ValueError('A source commit is required for version tags')
    with stage('verify_release_inputs', root=root):
        groups = local_groups(root)
    for version, group in sorted(groups.items(), key=lambda pair: tuple(map(int, pair[0].split('.')))):
        with stage('read_existing_release', version=version):
            release = get_release(repository, version)
        existing_manifests = existing_release_manifests(release) if release else []
        manifests = add_native_source_links(add_linux_rpm_links([*existing_manifests, *group['manifests']]))
        title, body = release_text(version, manifests)
        if not release:
            release = gh('api', f'repos/{repository}/releases', '--method', 'POST', '--input', '-',
                         data={'tag_name': version, 'target_commitish': target, 'name': title,
                               'body': body,
                               'draft': True, 'prerelease': False})
        assets = {asset['name']: asset for asset in release['assets']}
        known = {browser_asset_name(item['platform'], item['architecture'], item['channel'], item['version'])
                 for item in [*existing_manifests, *group['manifests']]}
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
        with stage('publish_release', version=version):
            gh('api', f'repos/{repository}/releases/{release["id"]}', '--method', 'PATCH', '--input', '-',
               data={'name': title, 'body': body, 'draft': False, 'prerelease': False, 'make_latest': 'false'})
        log_event('release_published', version=version, title=title, uploaded_archive_count=len(group['assets']))
    if not groups:
        log_event('no_new_archives')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('dist'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'), required=not bool(os.environ.get('GITHUB_REPOSITORY')))
    parser.add_argument('--target', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--preview', action='store_true', help='Print titles and coverage without writing to GitHub')
    args = parser.parse_args()
    log_runtime(role='publish')
    if args.preview:
        for version, group in local_groups(args.input).items():
            title, body = release_text(version, add_native_source_links(add_linux_rpm_links(group['manifests'])))
            print(title + '\n\n' + body)
    else:
        publish(args.repository, args.target, args.input)


if __name__ == '__main__':
    main()
