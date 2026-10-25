"""Resolve fixed-version RPM links from Google's official YUM metadata."""
from functools import lru_cache
import gzip
import hashlib
from pathlib import PurePosixPath
import urllib.parse
import xml.etree.ElementTree as ET

from chrome_archive import OPENER, VERSION, google_https, validate_target


def repository_url(root, href):
    parsed = urllib.parse.urlsplit(href)
    if (not href or parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
            or href.startswith('/') or '..' in PurePosixPath(href).parts):
        raise ValueError('Unsafe RPM repository path')
    return google_https(urllib.parse.urljoin(root, href))


def read_metadata(url):
    with OPENER.open(url, timeout=60) as response:
        data = response.read(8 * 1024**2 + 1)
    if len(data) > 8 * 1024**2:
        raise ValueError('RPM metadata exceeds size limit')
    return data


@lru_cache(maxsize=2)
def rpm_sources(arch):
    validate_target('linux', arch)
    rpm_arch = {'x64': 'x86_64', 'arm64': 'aarch64'}[arch]
    root = f'https://dl.google.com/linux/chrome/rpm/stable/{rpm_arch}/'
    repo_ns = {'r': 'http://linux.duke.edu/metadata/repo'}
    repomd = ET.fromstring(read_metadata(root + 'repodata/repomd.xml'))
    primary = repomd.find("r:data[@type='primary']", repo_ns)
    if primary is None:
        raise ValueError('RPM primary metadata is missing')
    location = primary.find('r:location', repo_ns)
    checksum = primary.find('r:checksum', repo_ns)
    if location is None or checksum is None or checksum.get('type') != 'sha256':
        raise ValueError('RPM primary metadata requires SHA-256')
    data = read_metadata(repository_url(root, location.get('href', '')))
    if (len(data) != int(primary.findtext('r:size', namespaces=repo_ns))
            or hashlib.sha256(data).hexdigest() != checksum.text):
        raise ValueError('RPM primary metadata size/SHA256 mismatch')
    ns = {'p': 'http://linux.duke.edu/metadata/common'}
    packages = ET.fromstring(gzip.decompress(data))
    sources = {}
    for package in packages.findall('p:package', ns):
        if (package.findtext('p:name', namespaces=ns) != 'google-chrome-stable'
                or package.findtext('p:arch', namespaces=ns) != rpm_arch):
            continue
        version = package.find('p:version', ns).get('ver', '')
        href = package.find('p:location', ns).get('href', '')
        if not VERSION.fullmatch(version):
            raise ValueError('Invalid RPM Chrome version')
        # Reject floating "current" links even if a repository changes format.
        if not PurePosixPath(href).name.startswith(f'google-chrome-stable-{version}-') or not href.endswith(f'.{rpm_arch}.rpm'):
            raise ValueError('RPM location is not versioned for the requested architecture')
        sources[version] = repository_url(root, href)
    return sources
