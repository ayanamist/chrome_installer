#!/usr/bin/env python3
"""Publish version tags and merge channel/platform coverage into one Release."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from chrome_archive import CHANNELS, VERSION, get_release, gh, sha256, validate_target


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
    rows = sorted({(item['channel'], item['platform'], item['architecture']) for item in manifests})
    channels = [CHANNELS[channel] for channel in CHANNELS if any(row[0] == channel for row in rows)]
    title = version + ' · ' + ' / '.join(channels)
    body = f'# Chrome {version}\n\n'
    body += 'Official Google download payloads, archived without executing an installer.\n\n'
    body += '| Channel | Platform | Architecture |\n|---|---|---|\n'
    for channel, platform, arch in rows:
        body += f'| {CHANNELS[channel]} | {dict(windows="Windows", macos="macOS", linux="Linux")[platform]} | {arch.upper()} |\n'
    body += '\n## Assets\n\n'
    body += '- `*-browser.7z`: extracted Windows browser files.\n'
    body += '- `*-browser.tar.xz`: extracted macOS/Linux files, preserving Unix modes and symbolic links.\n'
    body += '- Original Google files are also retained. Names containing `uncompressed` are compressed directly to `.xz` (original filename + `.xz`). Other originals are uploaded unchanged.\n'
    body += '- Asset prefixes identify platform, architecture and channel.\n'
    body += '- `*-manifest.json` and `*-SHA256SUMS`: source URL, official package hash, verification results and release-asset hashes.\n\n'
    body += 'Linux has no official Extended Stable channel. Extracted archives do not install dependencies or updater integration.\n'
    return title, body


def local_groups(root):
    groups = {}
    for path in root.glob('*/*-manifest.json'):
        item = validate_manifest(json.loads(path.read_text(encoding='utf-8')), path.parent.name)
        files = [path.parent / entry['name'] for entry in item['files']]
        for entry, file in zip(item['files'], files):
            if file.parent != path.parent or file.name != entry['name'] or not file.is_file() or file.stat().st_size != entry['size'] or sha256(file) != entry['sha256']:
                raise ValueError(f'Invalid or missing release asset: {file}')
        checksum = path.with_name(path.name.removesuffix('-manifest.json') + '-SHA256SUMS')
        if not checksum.is_file():
            raise ValueError(f'Missing checksum manifest: {checksum}')
        expected = ''.join(f'{sha256(file)}  {file.name}\n' for file in [*files, path])
        if checksum.read_text(encoding='utf-8') != expected:
            raise ValueError(f'Checksum manifest mismatch: {checksum}')
        group = groups.setdefault(item['version'], {'manifests': [], 'assets': {}})
        group['manifests'].append(item)
        for file in [*files, path, checksum]:
            group['assets'][file.name] = file
    return groups


def publish(repository, target, root):
    if not target:
        raise ValueError('A source commit is required for version tags')
    groups = local_groups(root)
    for version, group in sorted(groups.items(), key=lambda pair: tuple(map(int, pair[0].split('.')))):
        release = get_release(repository, version)
        existing_manifests = []
        if release:
            with tempfile.TemporaryDirectory() as directory:
                names = [asset['name'] for asset in release['assets'] if asset['name'].endswith('-manifest.json')]
                if names:
                    subprocess.run(['gh', 'release', 'download', version, '--repo', repository,
                                    '--pattern', '*-manifest.json', '--dir', directory], check=True)
                    for path in Path(directory).glob('*-manifest.json'):
                        existing_manifests.append(validate_manifest(json.loads(path.read_text(encoding='utf-8')), version))
        title, body = release_text(version, [*existing_manifests, *group['manifests']])
        if not release:
            release = gh('api', f'repos/{repository}/releases', '--method', 'POST', '--input', '-',
                         data={'tag_name': version, 'target_commitish': target, 'name': title,
                               'body': 'Uploading archive assets; coverage is finalized after successful upload.',
                               'draft': True, 'prerelease': False})
        assets = {asset['name']: asset for asset in release['assets']}
        for name, path in group['assets'].items():
            if name in assets:
                # Never overwrite an existing version asset, including partial retries.
                asset = assets[name]
                expected = 'sha256:' + sha256(path)
                if asset.get('size') != path.stat().st_size or asset.get('digest') != expected:
                    raise ValueError(f'Existing immutable asset differs: {version}/{name}')
                continue
            subprocess.run(['gh', 'release', 'upload', version, str(path), '--repo', repository], check=True)
        # Update name/coverage and publish only after all local assets uploaded.
        gh('api', f'repos/{repository}/releases/{release["id"]}', '--method', 'PATCH', '--input', '-',
           data={'name': title, 'body': body, 'draft': False, 'prerelease': False, 'make_latest': 'false'})
        print(f'Published {version}: {title}', flush=True)
    if not groups:
        print('No new archive assets; existing releases are already complete.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('dist'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'), required=not bool(os.environ.get('GITHUB_REPOSITORY')))
    parser.add_argument('--target', default=os.environ.get('GITHUB_SHA'))
    parser.add_argument('--preview', action='store_true', help='Print titles and coverage without writing to GitHub')
    args = parser.parse_args()
    if args.preview:
        for version, group in local_groups(args.input).items():
            title, body = release_text(version, group['manifests'])
            print(title + '\n\n' + body)
    else:
        publish(args.repository, args.target, args.input)


if __name__ == '__main__':
    main()
