#!/usr/bin/env python3
"""Refresh the README table of current Release pages from this run's discovery records."""
import argparse
import json
import os
from pathlib import Path

from archive_logging import log_event
from release_metadata import ARCHITECTURES, CHANNELS, VERSION, validate_target

START = '<!-- releases:start -->'
END = '<!-- releases:end -->'
PLATFORMS = {'windows': 'Windows', 'macos': 'macOS', 'linux': 'Linux'}


def read_discovery(root):
    """Return {(platform, arch): {channel: version}} from *-discovery.json files."""
    result = {}
    for path in sorted(root.glob('*-discovery.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        validate_target(value.get('platform'), value.get('architecture'))
        channels = value.get('channels')
        if not isinstance(channels, dict):
            raise ValueError(f'Invalid discovery channels: {path}')
        for channel, version in channels.items():
            if channel not in CHANNELS or not isinstance(version, str) or not VERSION.fullmatch(version):
                raise ValueError(f'Invalid discovery entry: {path}')
        result[(value['platform'], value['architecture'])] = channels
    return result


def release_table(repository, discovered):
    """Render every platform/architecture row with one Release link per channel."""
    base = f'https://github.com/{repository}/releases/tag/'
    lines = ['| 平台 | 架构 | ' + ' | '.join(CHANNELS.values()) + ' |',
             '|---|---|' + '---|' * len(CHANNELS)]
    for platform, architectures in ARCHITECTURES.items():
        for arch in architectures:
            channels = discovered.get((platform, arch), {})
            cells = []
            for channel in CHANNELS:
                version = channels.get(channel)
                if version:
                    cells.append(f'[{version}]({base}{version})')
                else:
                    cells.append('官方不提供' if platform == 'linux' and channel == 'extended' else '—')
            lines.append(f'| {PLATFORMS[platform]} | {arch.upper()} | ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def update_readme(text, table):
    if text.count(START) != 1 or text.count(END) != 1 or text.index(START) > text.index(END):
        raise ValueError('README must contain exactly one releases marker pair')
    head, rest = text.split(START)
    _, tail = rest.split(END)
    return f'{head}{START}\n{table}\n{END}{tail}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('dist'))
    parser.add_argument('--readme', type=Path, default=Path('README.md'))
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'),
                        required=not bool(os.environ.get('GITHUB_REPOSITORY')))
    args = parser.parse_args()
    discovered = read_discovery(args.input)
    if not discovered:
        log_event('readme_update_skipped', reason='no_discovery')
        return
    old = args.readme.read_text(encoding='utf-8')
    # Keep rows for targets absent from this run unchanged rather than blanking them.
    previous = parse_table(old, args.repository)
    for key, channels in previous.items():
        discovered.setdefault(key, channels)
    new = update_readme(old, release_table(args.repository, discovered))
    if new != old:
        args.readme.write_text(new, encoding='utf-8')
    log_event('readme_releases_updated', changed=new != old, targets=len(discovered))


def parse_table(text, repository):
    """Recover existing links so a partial run does not erase other rows."""
    if START not in text or END not in text:
        return {}
    block = text.split(START, 1)[1].split(END, 1)[0]
    base = f'https://github.com/{repository}/releases/tag/'
    names = {name: key for key, name in PLATFORMS.items()}
    channels = list(CHANNELS)
    result = {}
    for line in block.splitlines():
        cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
        if len(cells) != 2 + len(channels) or cells[0] not in names:
            continue
        found = {}
        for channel, cell in zip(channels, cells[2:]):
            if cell.startswith('[') and f']({base}' in cell:
                found[channel] = cell[1:cell.index(']')]
        if found:
            result[(names[cells[0]], cells[1].lower())] = found
    return result


if __name__ == '__main__':
    main()
