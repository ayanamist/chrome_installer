#!/usr/bin/env python3
"""Archive official Chrome payloads without installing or running Chrome."""
import argparse
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

from archive_logging import log_context, log_event, log_runtime, path_bytes, stage

ENDPOINT = 'https://update.googleapis.com/service/update2/json'
DEB_ROOT = 'https://dl.google.com/linux/chrome/deb/'
UPDATER_VERSION = '157.0.8089.0'
CHANNELS = {'stable': 'Stable', 'extended': 'Extended Stable'}
ARCHITECTURES = {'windows': ('x86', 'x64', 'arm64'),
                 'macos': ('x64', 'arm64'), 'linux': ('x64', 'arm64')}
VERSION = re.compile(r'^\d+\.\d+\.\d+\.\d+$')
DOWNLOAD_PROGRESS_SECONDS = 15


def validate_target(platform, arch):
    if platform not in ARCHITECTURES or arch not in ARCHITECTURES[platform]:
        raise ValueError(f'Unsupported target: {platform}/{arch}')


def browser_asset_name(platform, arch, channel, version):
    suffix = '.7z' if platform == 'windows' else '.tar.xz'
    return f'chrome-{platform}-{arch}-{channel}-{version}-browser{suffix}'


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(value):
    if not value or PurePosixPath(value).name != value or PureWindowsPath(value).name != value or ':' in value or value in ('.', '..'):
        raise ValueError(f'Unsafe filename: {value!r}')
    return value


def google_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (
        host == 'google.com' or host.endswith('.google.com') or host.endswith('.gvt1.com')):
        raise ValueError(f'Unexpected download origin: {url}')
    return url


class GoogleRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        google_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(GoogleRedirects())


def gh(*args, data=None, allow_missing=False):
    result = subprocess.run(['gh', *args], input=json.dumps(data) if data is not None else None,
                            capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        if allow_missing and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(f'gh {args[0]} failed: {result.stderr.strip()}')
    return json.loads(result.stdout) if result.stdout.strip() else None


def get_release(repository, version):
    return gh('api', f'repos/{repository}/releases/tags/{version}', allow_missing=True)


@dataclass(frozen=True)
class Offer:
    platform: str
    arch: str
    channel: str
    version: str
    package_name: str
    size: int
    digest: str
    urls: tuple
    original_name: str
    arguments: str = ''

    def __post_init__(self):
        validate_target(self.platform, self.arch)
        if not VERSION.fullmatch(self.version) or self.channel not in CHANNELS:
            raise ValueError('Invalid version/channel')
        safe_name(self.package_name)
        safe_name(self.original_name)
        if not re.fullmatch('[a-f0-9]{64}', self.digest) or not 0 < self.size < 3 * 1024**3:
            raise ValueError('Invalid package hash/size')
        for url in self.urls:
            google_https(url)

    @property
    def prefix(self):
        return f'chrome-{self.platform}-{self.arch}-{self.channel}-{self.version}'

    @property
    def browser_asset(self):
        return browser_asset_name(self.platform, self.arch, self.channel, self.version)

    @property
    def manifest_name(self):
        return self.prefix + '-manifest.json'

    @property
    def asset_names(self):
        return [self.browser_asset]


def query_omaha(platform, arch, channel, *, accept_crx3=True, target_version=None):
    validate_target(platform, arch)
    if platform not in ('windows', 'macos') or channel not in CHANNELS:
        raise ValueError('Unsupported Omaha target')
    os_name = 'win' if platform == 'windows' else 'mac'
    app_id = '{8A69D345-D564-463C-AFF1-A69D9E530F96}' if platform == 'windows' else 'com.google.Chrome'
    app = {'appid': app_id, 'version': '0.0.0.0', 'release_channel': channel,
           'installsource': 'taggedmi', 'updatecheck': {'sameversionupdate': True}}
    if target_version is not None:
        if not VERSION.fullmatch(target_version):
            raise ValueError('Invalid target version')
        app['updatecheck'].update(targetversionprefix=target_version, rollback_allowed=True)
    if platform == 'windows':
        app['ap'] = '-arch_' + arch
    request = {'request': {'protocol': '3.1', 'ismachine': True,
                          'sessionid': '{' + str(uuid.uuid4()) + '}', 'requestid': '{' + str(uuid.uuid4()) + '}',
                          '@updater': 'updater', 'prodversion': UPDATER_VERSION,
                          'updaterversion': UPDATER_VERSION, '@os': os_name, 'arch': arch,
                          'os': {'platform': os_name, 'arch': arch,
                                 'version': '10.0.26100.0' if os_name == 'win' else '15.0.0'}, 'app': [app]}}
    if accept_crx3:
        request['request']['acceptformat'] = 'crx3'
    req = urllib.request.Request(ENDPOINT, data=json.dumps(request).encode(),
                                 headers={'Content-Type': 'application/json', 'X-Goog-Update-Interactivity': 'fg'})
    with OPENER.open(req, timeout=60) as response:
        raw = response.read(4 * 1024**2)
    answer = json.loads(raw[raw.index(b'{'):])['response']['app'][0]
    if answer.get('appid', '').lower() != app_id.lower() or answer.get('status') != 'ok':
        raise RuntimeError(f'Unexpected Omaha response: {answer}')
    update = answer.get('updatecheck', {})
    if not accept_crx3 and update.get('status') == 'noupdate':
        return None
    if update.get('status') != 'ok':
        raise RuntimeError(f'No full package offered for {platform}/{arch}/{channel}: {update.get("status")}')
    manifest = update['manifest']
    packages = manifest['packages']['package']
    if len(packages) != 1:
        raise ValueError('Expected one full package')
    package = packages[0]
    urls = [urllib.parse.urljoin(item['codebase'], urllib.parse.quote(safe_name(package['name'])))
            for item in update['urls']['url'] if item['codebase'].startswith('https://')]
    urls.sort(key=lambda url: urllib.parse.urlsplit(url).hostname != 'dl.google.com')
    original = manifest.get('run', package['name']) if platform == 'windows' else package['name']
    return Offer(platform, arch, channel, manifest['version'], package['name'], int(package['size']),
                 package['hash_sha256'].lower(), tuple(urls), original, manifest.get('arguments', ''))


def parse_deb_index(text, arch):
    validate_target('linux', arch)
    deb_arch = {'x64': 'amd64', 'arm64': 'arm64'}[arch]
    candidates = []
    for paragraph in text.split('\n\n'):
        fields = dict(line.split(': ', 1) for line in paragraph.splitlines() if ': ' in line and not line.startswith(' '))
        if fields.get('Package') != 'google-chrome-stable' or fields.get('Architecture') != deb_arch:
            continue
        filename = fields['Filename']
        if filename.startswith('/') or '..' in PurePosixPath(filename).parts:
            raise ValueError('Unsafe DEB repository path')
        version = fields['Version'].split('-', 1)[0]
        offer = Offer('linux', arch, 'stable', version, PurePosixPath(filename).name,
                      int(fields['Size']), fields['SHA256'], (DEB_ROOT + filename,), PurePosixPath(filename).name)
        candidates.append(offer)
    if not candidates:
        raise RuntimeError(f'No official Linux Stable package for {arch}')
    return max(candidates, key=lambda offer: tuple(map(int, offer.version.split('.'))))


def query_linux(arch):
    validate_target('linux', arch)
    deb_arch = {'x64': 'amd64', 'arm64': 'arm64'}[arch]
    with OPENER.open(DEB_ROOT + f'dists/stable/main/binary-{deb_arch}/Packages.gz', timeout=60) as response:
        data = gzip.decompress(response.read(8 * 1024**2)).decode()
    return parse_deb_index(data, arch)


def download(offer, cache):
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / offer.package_name
    if destination.exists():
        with stage('verify_cached_package', file=destination, input_bytes=offer.size):
            if destination.stat().st_size != offer.size or sha256(destination) != offer.digest:
                raise ValueError(f'Cached package fails verification: {destination}')
        log_event('cache_hit', file=destination)
        return destination
    errors = []
    for url in offer.urls:
        partial = destination.with_suffix(destination.suffix + '.partial')
        try:
            with stage('download', url=google_https(url), expected_bytes=offer.size, expected_sha256=offer.digest) as stats:
                digest = hashlib.sha256()
                count = 0
                stats['downloaded_bytes'] = 0
                started = last_progress = time.perf_counter()
                with OPENER.open(url, timeout=90) as response, partial.open('wb') as stream:
                    stats['http_status'] = getattr(response, 'status', None)
                    while chunk := response.read(1024**2):
                        count += len(chunk)
                        stats['downloaded_bytes'] = count
                        if count > offer.size:
                            raise ValueError('Package exceeds advertised size')
                        digest.update(chunk)
                        stream.write(chunk)
                        now = time.perf_counter()
                        if now - last_progress >= DOWNLOAD_PROGRESS_SECONDS:
                            log_event('download_progress', downloaded_bytes=count, total_bytes=offer.size,
                                      percent=round(count * 100 / offer.size, 1),
                                      average_MiB_per_second=round(count / 1024**2 / max(now - started, 0.001), 2))
                            last_progress = now
                stats['actual_sha256'] = digest.hexdigest()
                if count != offer.size or stats['actual_sha256'] != offer.digest:
                    raise ValueError('Package size/SHA256 mismatch')
                partial.replace(destination)
                stats.update(downloaded_bytes=count, sha256_verified=True,
                             average_MiB_per_second=round(count / 1024**2 / max(time.perf_counter() - started, 0.001), 2))
            return destination
        except Exception as error:
            partial.unlink(missing_ok=True)
            errors.append(str(error))
    raise RuntimeError(f'All downloads failed: {errors}')


def validate_crx(path):
    with path.open('rb') as stream:
        magic, version, length = struct.unpack('<4sII', stream.read(12))
    if magic != b'Cr24' or version != 3 or length + 12 >= path.stat().st_size:
        raise ValueError('Not a valid CRX3 container')
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if name.startswith('/') or '..' in parts or any(':' in part for part in parts):
                raise ValueError(f'Unsafe ZIP path: {name}')
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                link = archive.read(info).decode()
                if link.startswith('/') or '\\' in link:
                    raise ValueError(f'Unsafe ZIP symlink: {name}')
                # Relative '..' is normal in app bundles, but must stay in ZIP root.
                depth = len(parts) - 1
                for part in PurePosixPath(link).parts:
                    depth += -1 if part == '..' else 0 if part == '.' else 1
                    if depth < 0:
                        raise ValueError(f'Escaping ZIP symlink: {name}')


def extract_app_zip(package, destination):
    """Restore app-bundle links after ordinary files; preserve executable modes."""
    validate_crx(package)
    destination.mkdir()
    root = destination.resolve()
    links = []
    directory_modes = []
    with zipfile.ZipFile(package) as archive:
        link_names = {info.filename.rstrip('/') for info in archive.infolist()
                      if stat.S_ISLNK(info.external_attr >> 16)}
        for info in archive.infolist():
            parts = PurePosixPath(info.filename).parts
            if any('/'.join(parts[:i]) in link_names for i in range(1, len(parts))):
                raise ValueError('ZIP member traverses a symlink')
            target = destination.joinpath(*parts)
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                links.append((target, archive.read(info).decode()))
            elif info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                directory_modes.append((target, mode & 0o7777 or 0o755))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(mode & 0o7777 or 0o644)
        for target, link in links:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(link)
        for target, _ in links:
            if not target.resolve().is_relative_to(root):
                raise ValueError('App bundle symlink escapes extraction root')
        for target, mode in reversed(directory_modes):
            target.chmod(mode)


def seven_zip(command, *args, cwd=None):
    subprocess.run([command, *map(str, args), '-bso0', '-bsp0'], cwd=cwd, check=True)


def compress(command, source, destination):
    with stage('compress_7z', input_bytes=path_bytes(source), level=5, threads=2, output=destination) as stats:
        seven_zip(command, 'a', '-t7z', '-mx=5', '-mmt=2', '-snl', '-mtm=off', '-mta=off', '-mtc=off',
                  destination.resolve(), source.name, cwd=source.parent)
        stats['output_bytes'] = destination.stat().st_size
    with stage('verify_7z', file=destination, input_bytes=destination.stat().st_size):
        seven_zip(command, 't', destination.resolve())


def compress_tar_xz(source, destination):
    def normalize(info):
        # Stable ownership/time without changing permissions, links or contents.
        info.uid = info.gid = 0
        info.uname = info.gname = ''
        info.mtime = 0
        info.pax_headers = {}
        return info
    with stage('compress_browser_tar_xz', input_bytes=path_bytes(source), preset=6, output=destination) as stats:
        with tarfile.open(destination, 'w:xz', preset=6, dereference=False) as archive:
            archive.add(source, arcname=source.name, filter=normalize)
        stats['output_bytes'] = destination.stat().st_size
    # Read every member to the end to verify XZ checksum and archive contents.
    with stage('verify_browser_tar_xz', file=destination, input_bytes=destination.stat().st_size) as stats:
        files = links = 0
        with tarfile.open(destination, 'r:xz') as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        expected = sha256(source.parent / member.name)
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                            raise ValueError('tar.xz round-trip verification failed')
                    files += 1
                elif member.issym():
                    if os.readlink(source.parent / member.name) != member.linkname:
                        raise ValueError('tar.xz symlink verification failed')
                    links += 1
        stats.update(verified_files=files, verified_symlinks=links)


def verify_pe_version(path, version, command):
    result = subprocess.run([command, 'l', '-slt', str(path)], check=True,
                            capture_output=True, text=True, encoding='utf-8')
    match = re.search(r'^ProductVersion:\s*([^\r\n]+)', result.stdout, re.M)
    if not match or match[1].strip() != version:
        raise ValueError('PE product version differs from manifest')


def validate_binary_arch(path, platform, arch):
    validate_target(platform, arch)
    with path.open('rb') as stream:
        header = stream.read(4096)
    if platform == 'windows':
        if header[:2] != b'MZ':
            raise ValueError('Not a PE executable')
        offset = struct.unpack_from('<I', header, 0x3c)[0]
        with path.open('rb') as stream:
            stream.seek(offset)
            pe = stream.read(6)
        if pe[:4] != b'PE\0\0' or struct.unpack_from('<H', pe, 4)[0] != {'x86': 0x014c, 'x64': 0x8664, 'arm64': 0xaa64}[arch]:
            raise ValueError(f'PE architecture does not match {arch}')
    elif platform == 'linux':
        if header[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', header, 18)[0] != {'x64': 62, 'arm64': 183}[arch]:
            raise ValueError(f'ELF architecture does not match {arch}')
    else:
        expected = {'x64': 0x01000007, 'arm64': 0x0100000c}[arch]
        magic = header[:4]
        if magic in (b'\xca\xfe\xba\xbe', b'\xca\xfe\xba\xbf'):
            count = struct.unpack_from('>I', header, 4)[0]
            stride = 20 if magic[-1] == 0xbe else 32
            types = [struct.unpack_from('>I', header, 8 + i * stride)[0] for i in range(count)]
        elif magic == b'\xcf\xfa\xed\xfe':
            types = [struct.unpack_from('<I', header, 4)[0]]
        else:
            raise ValueError('Unrecognized Mach-O header')
        if expected not in types:
            raise ValueError(f'Mach-O does not contain {arch}')


def extract_deb(package, workspace, command):
    # 7-Zip reads the ar/DEB wrapper. tarfile extracts payload without maintainer scripts.
    outer = workspace / 'deb'
    outer.mkdir()
    seven_zip(command, 'x', package.resolve(), '-o' + str(outer.resolve()), '-y')
    candidates = list(outer.glob('data.tar.*')) + list(outer.glob('data.tar'))
    if len(candidates) != 1:
        raise ValueError('Expected one DEB data archive')
    payload = workspace / 'payload'
    payload.mkdir()
    with tarfile.open(candidates[0]) as archive:
        members = [member for member in archive.getmembers()
                   if PurePosixPath(member.name).as_posix().removeprefix('./').startswith('opt/google/chrome/')
                   or PurePosixPath(member.name).as_posix().removeprefix('./') == 'opt/google/chrome']
        # Preserve executable/setuid modes from Google's package; reject escaping paths/links.
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts:
                raise ValueError('Unsafe DEB member')
            if member.issym() or member.islnk():
                if PurePosixPath(member.linkname).is_absolute() or '..' in PurePosixPath(member.linkname).parts:
                    raise ValueError('Unexpected DEB payload link')
        archive.extractall(payload, members=members, filter='fully_trusted')
    return payload / 'opt/google/chrome'


def build(offer, package, output, command):
    import plistlib
    assets = output / offer.version
    assets.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.chrome-archive-', dir=output.resolve().parent) as directory:
        workspace = Path(directory)
        if offer.platform == 'windows':
            original = workspace / offer.original_name
            with stage('extract_installer', package=package, input_bytes=package.stat().st_size) as stats:
                validate_crx(package)
                with zipfile.ZipFile(package) as archive:
                    matches = [info for info in archive.infolist() if info.filename == offer.original_name]
                    if len(matches) != 1:
                        raise ValueError('Expected one manifest installer')
                    with archive.open(matches[0]) as source, original.open('wb') as destination:
                        shutil.copyfileobj(source, destination)
                stats['output_bytes'] = original.stat().st_size
            with stage('verify_pe_version', file=original):
                verify_pe_version(original, offer.version, command)
            payload = workspace / 'payload'
            with stage('extract_browser', input_bytes=original.stat().st_size) as stats:
                seven_zip(command, 'x', original, '-o' + str(payload), '-y')
                stats['output_bytes'] = path_bytes(payload)
            browser = payload / 'Chrome-bin'
            executable = browser / 'chrome.exe'
            if not (browser / offer.version / 'chrome.dll').is_file():
                raise ValueError('Chrome payload version directory missing')
        elif offer.platform == 'macos':
            payload = workspace / 'payload'
            with stage('extract_app_bundle', input_bytes=package.stat().st_size) as stats:
                extract_app_zip(package, payload)
                stats['output_bytes'] = path_bytes(payload)
            apps = list(payload.glob('*.app'))
            if len(apps) != 1:
                raise ValueError('Expected one macOS app bundle')
            browser = apps[0]
            executable = browser / 'Contents/MacOS/Google Chrome'
            with stage('verify_bundle_version'):
                info = plistlib.loads((browser / 'Contents/Info.plist').read_bytes())
                if info.get('CFBundleShortVersionString') != offer.version:
                    raise ValueError('macOS bundle version differs from manifest')
        else:
            with stage('extract_deb_payload', input_bytes=package.stat().st_size) as stats:
                browser = extract_deb(package, workspace, command)
                stats['output_bytes'] = path_bytes(browser)
            executable = browser / 'chrome'
        with stage('verify_binary_architecture', file=executable):
            validate_binary_arch(executable, offer.platform, offer.arch)
        browser_target = assets / offer.browser_asset
        if offer.platform == 'windows':
            compress(command, browser, browser_target)
        else:
            compress_tar_xz(browser, browser_target)
        with stage('hash_browser_archive', file=browser_target, input_bytes=browser_target.stat().st_size):
            browser_hash = sha256(browser_target)
        metadata = {'schema_version': 1, 'version': offer.version,
                    'channel': offer.channel, 'channel_name': CHANNELS[offer.channel],
                    'platform': offer.platform, 'architecture': offer.arch,
                    'source': {'url': offer.urls[0], 'package': offer.package_name,
                               'sha256': offer.digest, 'size': offer.size,
                               'original_name': offer.original_name, 'arguments_not_executed': offer.arguments},
                    'native_signature_verification': 'not performed; package SHA256 verified against official HTTPS manifest',
                    'binary_architecture_verified': True,
                    'installer_executed': False,
                    'files': [{'name': browser_target.name, 'size': browser_target.stat().st_size,
                               'sha256': browser_hash}]}
        manifest_target = assets / offer.manifest_name
        write_json(manifest_target, metadata)
        log_event('archive_ready', file=browser_target.name, bytes=browser_target.stat().st_size,
                  sha256=browser_hash, official_source=offer.urls[0])
    # The small manifest travels inside the temporary Actions artifact only.
    return [browser_target, manifest_target]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform', choices=['windows', 'macos', 'linux'], required=True)
    parser.add_argument('--arch', choices=['x86', 'x64', 'arm64'], required=True)
    parser.add_argument('--channel', choices=['stable', 'extended', 'all'], default='all')
    parser.add_argument('--output', type=Path, default=Path('dist'))
    parser.add_argument('--cache', type=Path, default=Path('.cache/packages'))
    parser.add_argument('--seven-zip', default='7z' if os.name == 'nt' else '7zz')
    parser.add_argument('--repository', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--query-only', action='store_true')
    args = parser.parse_args()
    try:
        validate_target(args.platform, args.arch)
    except ValueError as error:
        parser.error(str(error))
    if args.platform == 'linux' and args.channel == 'extended':
        parser.error('Google does not offer Extended Stable for Linux')
    if not args.query_only and not shutil.which(args.seven_zip):
        parser.error('7-Zip is required; install it before running this script')
    args.output.mkdir(parents=True, exist_ok=True)
    log_runtime(seven_zip=args.seven_zip, xz_preset=6, seven_zip_level=5, seven_zip_threads=2)
    built = []
    channels = ['stable'] if args.platform == 'linux' else list(CHANNELS) if args.channel == 'all' else [args.channel]
    for channel in channels:
        with log_context(platform=args.platform, architecture=args.arch, channel=channel):
            with stage('query_official_manifest'):
                offer = query_linux(args.arch) if args.platform == 'linux' else query_omaha(args.platform, args.arch, channel)
            log_event('offer', **offer.__dict__)
            if args.query_only:
                continue
            with log_context(version=offer.version):
                with stage('channel_total'):
                    if args.repository:
                        with stage('check_existing_release'):
                            release = get_release(args.repository, offer.version)
                        names = {asset['name'] for asset in release['assets']} if release else set()
                        if not (set(offer.asset_names) - names):
                            log_event('already_archived', file=offer.browser_asset)
                            continue
                    package = download(offer, args.cache)
                    built.extend(build(offer, package, args.output, args.seven_zip))
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(f'has_assets={str(bool(built)).lower()}\n')
    log_event('archive_job_complete', release_assets=len(built) // 2, internal_manifests=len(built) // 2,
              installers_executed=False)


if __name__ == '__main__':
    main()
