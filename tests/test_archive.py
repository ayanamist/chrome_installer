import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chrome_archive as archive
import publish_releases as publish


def offer(**changes):
    values = dict(platform='windows', arch='x64', channel='stable', version='156.0.8078.48',
                  package_name='payload.crx3', size=10, digest='a' * 64,
                  urls=('https://dl.google.com/payload.crx3',),
                  original_name='156.0.8078.48_chrome_installer_uncompressed.exe')
    values.update(changes)
    return archive.Offer(**values)


class SourceTests(unittest.TestCase):
    def test_uncompressed_original_gets_extra_xz_suffix_and_arch_prefix(self):
        item = offer()
        self.assertTrue(item.original_asset.endswith(item.original_name + '.xz'))
        self.assertNotEqual(item.original_asset, offer(arch='arm64').original_asset)
        raw = offer(platform='linux', original_name='google-chrome.deb')
        self.assertTrue(raw.original_asset.endswith('.deb'))
        self.assertFalse(raw.original_asset.endswith('.deb.7z'))
        self.assertTrue(raw.browser_asset.endswith('.tar.xz'))
        self.assertTrue(item.browser_asset.endswith('.7z'))

    def test_xz_original_round_trip_and_tar_unix_metadata(self):
        import lzma
        import tarfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Chrome.app'
            source.mkdir()
            binary = source / 'binary'
            binary.write_bytes(b'original bytes' * 50)
            binary.chmod(0o755)
            (source / 'Current').symlink_to('binary')
            archive.compress_file_xz(binary, root / 'binary.xz')
            self.assertEqual(lzma.open(root / 'binary.xz', 'rb').read(), binary.read_bytes())
            archive.compress_tar_xz(source, root / 'app.tar.xz')
            with tarfile.open(root / 'app.tar.xz') as result:
                self.assertEqual(result.getmember('Chrome.app/binary').mode, 0o755)
                self.assertTrue(result.getmember('Chrome.app/Current').issym())
                self.assertEqual(result.getmember('Chrome.app/Current').linkname, 'binary')

    def test_rejects_unsafe_origins_and_names(self):
        for value in ['http://dl.google.com/x', 'https://google.com.evil.test/x', 'https://user:pass@dl.google.com/x']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.google_https(value)
        for value in ['../x.exe', 'C:\\x.exe', 'a/b.exe', 'a\\b.exe', '..']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                archive.safe_name(value)

    def test_deb_index_uses_matching_package_arch_and_highest_version(self):
        text = '\n\n'.join(f'Package: google-chrome-stable\nVersion: {v}-1\nArchitecture: {a}\nFilename: pool/google_{v}_{a}.deb\nSize: 10\nSHA256: {"a" * 64}'
                           for v, a in [('155.0.0.1', 'amd64'), ('156.0.0.1', 'amd64'), ('157.0.0.1', 'arm64')])
        self.assertEqual(archive.parse_deb_index(text, 'x64').version, '156.0.0.1')
        self.assertEqual(archive.parse_deb_index(text, 'arm64').version, '157.0.0.1')

    def test_crx_rejects_zip_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.crx3'
            path.write_bytes(struct.pack('<4sII', b'Cr24', 3, 0))
            with zipfile.ZipFile(path, 'a') as package:
                package.writestr('../escape', b'x')
            with self.assertRaisesRegex(ValueError, 'Unsafe ZIP path'):
                archive.validate_crx(path)

    def test_architecture_is_read_without_running_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'chrome'
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H', header, 18, 183)
            path.write_bytes(header)
            archive.validate_binary_arch(path, 'linux', 'arm64')
            with self.assertRaises(ValueError):
                archive.validate_binary_arch(path, 'linux', 'x64')

    def test_app_bundle_link_chains_and_executable_modes_are_preserved(self):
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'app.crx3'
            path.write_bytes(struct.pack('<4sII', b'Cr24', 3, 0))
            with zipfile.ZipFile(path, 'a') as package:
                binary = zipfile.ZipInfo('Chrome.app/Versions/1/Resources/helper')
                binary.external_attr = (stat.S_IFREG | 0o755) << 16
                package.writestr(binary, b'data')
                for name, target in [('Chrome.app/Versions/Current', '1'), ('Chrome.app/Resources', 'Versions/Current/Resources')]:
                    link = zipfile.ZipInfo(name)
                    link.external_attr = (stat.S_IFLNK | 0o777) << 16
                    package.writestr(link, target)
            archive.extract_app_zip(path, root / 'out')
            helper = root / 'out/Chrome.app/Resources/helper'
            self.assertEqual(helper.read_bytes(), b'data')
            self.assertEqual(helper.stat().st_mode & 0o777, 0o755)

    def test_cached_download_is_rehashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'payload.crx3').write_bytes(b'wrong data')
            with self.assertRaisesRegex(ValueError, 'Cached package fails'):
                archive.download(offer(), root)


class ReleaseTests(unittest.TestCase):
    def manifests(self):
        return [dict(schema_version=1, version='156.0.0.1', channel=channel,
                     platform=platform, architecture=arch)
                for channel, platform, arch in [('stable', 'windows', 'x64'), ('extended', 'macos', 'arm64')]]

    def test_same_version_merges_channels_in_title_and_actual_coverage(self):
        title, body = publish.release_text('156.0.0.1', self.manifests())
        self.assertEqual(title, '156.0.0.1 · Stable / Extended Stable')
        self.assertIn('| Stable | Windows | X64 |', body)
        self.assertIn('| Extended Stable | macOS | ARM64 |', body)
        self.assertNotIn('| Stable | Linux |', body)

    def test_no_fake_linux_extended_stable(self):
        value = dict(self.manifests()[1], platform='linux')
        with self.assertRaises(ValueError):
            publish.validate_manifest(value, value['version'])

    def test_publish_upload_failure_keeps_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / 'asset.7z'
            asset.write_bytes(b'data')
            group = {'156.0.0.1': {'manifests': self.manifests(), 'assets': {'asset.7z': asset}}}
            with patch.object(publish, 'local_groups', return_value=group), patch.object(publish, 'get_release', return_value=None), patch.object(publish, 'gh', return_value={'id': 1, 'assets': []}) as api, patch.object(publish.subprocess, 'run', side_effect=RuntimeError('upload failed')):
                with self.assertRaises(RuntimeError):
                    publish.publish('owner/repo', 'commit', Path(directory))
                self.assertEqual(api.call_count, 1)
                self.assertTrue(api.call_args.kwargs['data']['draft'])

    def test_retry_keeps_existing_assets_and_publishes_after_uploads(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / 'asset.7z'
            asset.write_bytes(b'data')
            group = {'156.0.0.1': {'manifests': self.manifests(), 'assets': {'asset.7z': asset}}}
            release = {'id': 1, 'assets': [{'name': asset.name, 'size': 4, 'digest': 'sha256:' + archive.sha256(asset)}]}
            with patch.object(publish, 'local_groups', return_value=group), patch.object(publish, 'get_release', return_value=release), patch.object(publish, 'gh') as api, patch.object(publish.subprocess, 'run') as commands:
                publish.publish('owner/repo', 'commit', Path(directory))
                commands.assert_not_called()
                self.assertFalse(api.call_args.kwargs['data']['draft'])
                self.assertEqual(api.call_args.kwargs['data']['name'], '156.0.0.1 · Stable / Extended Stable')

    def test_existing_assets_never_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / 'asset.7z'
            asset.write_bytes(b'data')
            group = {'156.0.0.1': {'manifests': self.manifests(), 'assets': {'asset.7z': asset}}}
            release = {'id': 1, 'assets': [{'name': asset.name, 'size': 4, 'digest': 'sha256:' + '0' * 64}]}
            with patch.object(publish, 'local_groups', return_value=group), patch.object(publish, 'get_release', return_value=release), patch.object(publish, 'gh') as api:
                with self.assertRaisesRegex(ValueError, 'Existing immutable asset differs'):
                    publish.publish('owner/repo', 'commit', Path(directory))
                api.assert_not_called()


if __name__ == '__main__':
    unittest.main()
