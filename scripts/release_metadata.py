"""Release provenance shared by archive discovery and publishing."""
import json
import re
import urllib.parse

CHANNELS = {'stable': 'Stable', 'extended': 'Extended Stable'}
ARCHITECTURES = {'windows': ('x86', 'x64', 'arm64'),
                 'macos': ('x64', 'arm64'), 'linux': ('x64', 'arm64')}
VERSION = re.compile(r'^\d+\.\d+\.\d+\.\d+$')
METADATA = re.compile(r'<!-- chrome-archive-metadata-v1\n(.*?)\n-->', re.S)


def validate_target(platform, arch):
    if platform not in ARCHITECTURES or arch not in ARCHITECTURES[platform]:
        raise ValueError(f'Unsupported target: {platform}/{arch}')


def google_https(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    if parsed.scheme != 'https' or parsed.username or parsed.password or not (
        host == 'google.com' or host.endswith('.google.com') or host.endswith('.gvt1.com')):
        raise ValueError(f'Unexpected download origin: {url}')
    return url


def browser_asset_name(platform, arch, channel, version, digest=None):
    suffix = '.7z' if platform == 'windows' else '.tar.xz'
    qualifier = f'-{channel}' if channel else ''
    content = f'-{digest}' if digest else ''
    return f'chrome-{platform}-{arch}{qualifier}-{version}{content}-browser{suffix}'


def manifest_asset_name(item):
    return item.get('browser_asset') or browser_asset_name(
        item['platform'], item['architecture'], item['channel'], item['version'])


def validate_manifest(value, version):
    if value.get('schema_version') != 1 or value.get('version') != version or not VERSION.fullmatch(version):
        raise ValueError('Invalid archive manifest version')
    validate_target(value.get('platform'), value.get('architecture'))
    if value.get('channel') not in CHANNELS:
        raise ValueError('Invalid channel')
    if value['platform'] == 'linux' and value['channel'] != 'stable':
        raise ValueError('Linux Extended Stable is unsupported')
    for key in ('url', 'native_url', 'rpm_url'):
        if key in value.get('source', {}):
            google_https(value['source'][key])
    source = value.get('source', {})
    if 'sha256' in source and not re.fullmatch('[a-f0-9]{64}', source['sha256']):
        raise ValueError('Invalid source SHA-256')
    if 'size' in source and (type(source['size']) is not int or source['size'] <= 0):
        raise ValueError('Invalid source size')
    if 'browser_asset' in value:
        prefix = browser_asset_name(value['platform'], value['architecture'], None, version)
        pattern = re.escape(prefix).replace(re.escape('-browser'), r'(?:-[a-f0-9]{64})?-browser')
        # Another channel's legacy asset may be shared after verifying its digest.
        legacy_names = {browser_asset_name(value['platform'], value['architecture'], channel, version)
                        for channel in CHANNELS}
        if value['browser_asset'] not in legacy_names and not re.fullmatch(pattern, value['browser_asset']):
            raise ValueError('Invalid browser asset name')
    return value


def compact_manifest(item):
    """Persist only public source facts and the selected attachment mapping."""
    value = {key: item[key] for key in ('schema_version', 'version', 'channel', 'platform', 'architecture')}
    value['browser_asset'] = manifest_asset_name(item)
    value['source'] = {key: item.get('source', {})[key]
                       for key in ('url', 'native_url', 'rpm_url', 'sha256', 'size')
                       if key in item.get('source', {})}
    return validate_manifest(value, item['version'])


def metadata_comment(manifests):
    values = [compact_manifest(item) for item in manifests]
    # Keep arbitrary URL characters from closing the HTML comment.
    payload = json.dumps(values, sort_keys=True, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e')
    return '<!-- chrome-archive-metadata-v1\n' + payload + '\n-->\n'


def existing_release_manifests(release):
    """Read explicit mappings, or recover provenance from historical tables."""
    assets = {asset['name'] for asset in release['assets']}
    body = release.get('body') or ''
    matches = METADATA.findall(body)
    if 'chrome-archive-metadata-v1' in body:
        if len(matches) != 1:
            raise ValueError('Invalid release provenance metadata')
        values = json.loads(matches[0])
        if not isinstance(values, list):
            raise ValueError('Invalid release provenance records')
        items = []
        for value in values:
            item = validate_manifest(value, release['tag_name'])
            if manifest_asset_name(item) in assets:
                items.append(item)
        return items
    pattern = re.compile(r'^\|\s*(Stable / Extended Stable|Stable|Extended Stable)\s*\|\s*(Windows|macOS|Linux)\s*\|\s*(X86|X64|ARM64)\s*\|\s*\[(Google original|CRX3|EXE|ZIP|DEB)\]\((https://[^\s)]+)\)(?:\s*·\s*\[(RPM|EXE|ZIP)\]\((https://[^\s)]+)\))?\s*\|\s*$', re.M)
    platforms = {'Windows': 'windows', 'macOS': 'macos', 'Linux': 'linux'}
    channels = {name: channel for channel, name in CHANNELS.items()}
    items = []
    for names, platform, arch, primary_label, url, extra_label, extra_url in pattern.findall(body):
        for name in names.split(' / '):
            item = validate_manifest({'schema_version': 1, 'version': release['tag_name'],
                                      'channel': channels[name], 'platform': platforms[platform],
                                      'architecture': arch.lower(), 'source': {'url': google_https(url)}}, release['tag_name'])
            if primary_label in ('EXE', 'ZIP'):
                if primary_label != {'Windows': 'EXE', 'macOS': 'ZIP', 'Linux': 'DEB'}[platform]:
                    raise ValueError('Native source format does not match platform')
                item['source']['native_url'] = google_https(url)
            if extra_url:
                if extra_label != {'Windows': 'EXE', 'macOS': 'ZIP', 'Linux': 'RPM'}[platform]:
                    raise ValueError('Alternate source format does not match platform')
                item['source']['rpm_url' if platform == 'Linux' else 'native_url'] = google_https(extra_url)
            if manifest_asset_name(item) in assets:
                items.append(item)
    return items
