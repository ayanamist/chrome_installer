#!/usr/bin/env python3
"""Use runner Windows 7-Zip, or install the pinned official portable console."""
import hashlib
import os
from pathlib import Path
import platform
import tarfile
import urllib.request

VERSION = '26.05'
ASSETS = {
    'Linux': ('7z2605-linux-x64.tar.xz', '22cf82fcc6eb7474f1d5bb4f02c0f8ebd573a718089d6833dc30f8350a3b232e'),
    'Darwin': ('7z2605-mac.tar.xz', '12ef6830317743cfd3f0ab1724bde43fcc270d8b6d8564f8c12c2a2b7f993570'),
}


def main():
    if os.name == 'nt':
        command = Path(os.environ['ProgramFiles']) / '7-Zip/7z.exe'
        if not command.is_file():
            raise RuntimeError('The Windows runner must provide 7-Zip')
    else:
        name, expected = ASSETS[platform.system()]
        root = Path(os.environ.get('RUNNER_TEMP', '.cache')) / '7zip-26.05'
        root.mkdir(parents=True, exist_ok=True)
        archive = root / name
        with urllib.request.urlopen(f'https://github.com/ip7z/7zip/releases/download/{VERSION}/{name}', timeout=90) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Official 7-Zip asset SHA256 mismatch')
        archive.write_bytes(data)
        with tarfile.open(archive) as package:
            package.extractall(root, filter='data')
        command = root / '7zz'
        command.chmod(0o755)
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'command={command}\n')
    print(command)


if __name__ == '__main__':
    main()
