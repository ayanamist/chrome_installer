#!/usr/bin/env python3
"""Publish version tags and merge channel/platform coverage into one Release."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

from chrome_archive import CHANNELS, VERSION, browser_asset_name, get_release, gh, google_https, sha256, validate_target
from archive_logging import log_context, log_event, log_runtime, stage


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
        url = entries[(channel, platform, arch)].get('source', {}).get('url')
        link = f'[Google original]({google_https(url)})' if url else '—'
        body += f'| {CHANNELS[channel]} | {dict(windows="Windows", macos="macOS", linux="Linux")[platform]} | {arch.upper()} | {link} |\n'
    body += '\n## Assets\n\n'
    body += '- `*-browser.7z`: extracted Windows browser files.\n'
    body += '- `*-browser.tar.xz`: extracted macOS/Linux files, preserving Unix modes and symbolic links.\n'
    body += '- Official original files are available from the Google links in the table above. Windows/macOS links point to the original updater CRX3; Linux links point to the original DEB.\n'
    body += '- Asset prefixes identify platform, architecture and channel.\n'
    body += '- Only the repacked browser archives are release attachments; original files and workflow metadata are not uploaded as release assets.\n\n'
    body += 'Linux has no official Extended Stable channel. Extracted archives do not install dependencies or updater integration.\n'
    return title, body


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
    pattern = re.compile(r'^\|\s*(Stable|Extended Stable)\s*\|\s*(Windows|macOS|Linux)\s*\|\s*(X86|X64|ARM64)\s*\|\s*\[Google original\]\((https://[^\s)]+)\)\s*\|\s*$', re.M)
    platforms = {'Windows': 'windows', 'macOS': 'macos', 'Linux': 'linux'}
    channels = {name: channel for channel, name in CHANNELS.items()}
    assets = {asset['name'] for asset in release['assets']}
    items = []
    for channel, platform, arch, url in pattern.findall(release.get('body') or ''):
        item = validate_manifest({'schema_version': 1, 'version': release['tag_name'],
                                  'channel': channels[channel], 'platform': platforms[platform],
                                  'architecture': arch.lower(), 'source': {'url': google_https(url)}}, release['tag_name'])
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
        title, body = release_text(version, [*existing_manifests, *group['manifests']])
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
            title, body = release_text(version, group['manifests'])
            print(title + '\n\n' + body)
    else:
        publish(args.repository, args.target, args.input)


if __name__ == '__main__':
    main()
