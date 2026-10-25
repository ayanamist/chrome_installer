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
import linux_sources


def offer(**changes):
    values = dict(platform='windows', arch='x64', channel='stable', version='156.0.8078.48',
                  package_name='payload.crx3', size=10, digest='a' * 64,
                  urls=('https://dl.google.com/payload.crx3',),
                  original_name='156.0.8078.48_chrome_installer_uncompressed.exe')
    values.update(changes)
    return archive.Offer(**values)


class SourceTests(unittest.TestCase):
    def test_already_archived_offer_still_records_current_version_for_refresh(self):
        import os
        item = offer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'github-output'
            with patch.object(sys, 'argv', ['chrome_archive.py', '--platform', 'windows', '--arch', 'x64',
                            '--channel', 'stable', '--output', str(root / 'dist'), '--repository', 'owner/repo']), \
                 patch.object(archive.shutil, 'which', return_value='/existing/7zz'), \
                 patch.object(archive, 'query_omaha', return_value=item), \
                 patch.object(archive, 'get_release', return_value={'assets': [{'name': item.browser_asset}]}), \
                 patch.object(archive, 'download') as download, patch.object(archive, 'build') as build, \
                 patch.object(archive, 'log_runtime'), patch.dict(os.environ, {'GITHUB_OUTPUT': str(output)}):
                archive.main()
            download.assert_not_called()
            build.assert_not_called()
            self.assertEqual(publish.discovered_versions(root / 'dist'), {item.version})
            self.assertIn('has_assets=false', output.read_text())
            self.assertIn('has_discovery=true', output.read_text())

    def test_discovery_combines_versions_and_rejects_linux_extended_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive.write_json(root / 'windows-discovery.json', dict(schema_version=1, platform='windows',
                architecture='x64', channels={'stable': '156.0.0.1', 'extended': '152.0.0.1'}))
            archive.write_json(root / 'linux-discovery.json', dict(schema_version=1, platform='linux',
                architecture='arm64', channels={'stable': '156.0.0.1'}))
            self.assertEqual(publish.discovered_versions(root), {'156.0.0.1', '152.0.0.1'})
            archive.write_json(root / 'linux-discovery.json', dict(schema_version=1, platform='linux',
                architecture='arm64', channels={'extended': '156.0.0.1'}))
            with self.assertRaisesRegex(ValueError, 'Linux Extended Stable'):
                publish.discovered_versions(root)

    def test_native_query_omits_crx3_and_uses_package_name_without_run(self):
        import io
        response = {'response': {'app': [{'appid': '{8A69D345-D564-463C-AFF1-A69D9E530F96}',
            'status': 'ok', 'updatecheck': {'status': 'ok',
            'urls': {'url': [{'codebase': 'https://dl.google.com/release/'}]},
            'manifest': {'version': '156.0.0.1', 'packages': {'package': [
                {'name': '156.0.0.1_chrome_installer_uncompressed.exe', 'size': 10, 'hash_sha256': 'a' * 64}]}}}}]}}
        with patch.object(archive.OPENER, 'open', return_value=io.BytesIO(json.dumps(response).encode())) as opened:
            result = archive.query_omaha('windows', 'x64', 'extended', allow_no_update=True, target_version='156.0.0.1')
        request = json.loads(opened.call_args.args[0].data)['request']
        self.assertNotIn('acceptformat', request)
        self.assertEqual(request['app'][0]['release_channel'], 'extended')
        self.assertEqual(request['app'][0]['ap'], '-arch_x64')
        self.assertEqual(request['app'][0]['updatecheck']['targetversionprefix'], '156.0.0.1')
        self.assertTrue(request['app'][0]['updatecheck']['rollback_allowed'])
        self.assertEqual(result.original_name, '156.0.0.1_chrome_installer_uncompressed.exe')
        response['response']['app'][0]['updatecheck'] = {'status': 'noupdate'}
        with patch.object(archive.OPENER, 'open', side_effect=lambda *a, **k: io.BytesIO(json.dumps(response).encode())):
            self.assertIsNone(archive.query_omaha('windows', 'x64', 'stable', allow_no_update=True))
            with self.assertRaisesRegex(RuntimeError, 'No full package offered'):
                archive.query_omaha('windows', 'x64', 'stable')

    def test_x86_requests_select_both_windows_channels(self):
        import io
        for channel in ('stable', 'extended'):
            response = {'response': {'app': [{'appid': '{8A69D345-D564-463C-AFF1-A69D9E530F96}',
                'status': 'ok', 'updatecheck': {'status': 'ok',
                'urls': {'url': [{'codebase': 'https://dl.google.com/release/'}]},
                'manifest': {'version': '156.0.0.1', 'run': 'installer_uncompressed.exe',
                'packages': {'package': [{'name': '156.0.0.1_chrome_installer_uncompressed.exe', 'size': 10, 'hash_sha256': 'a' * 64}]}}}}]}}
            with self.subTest(channel=channel), patch.object(archive.OPENER, 'open', return_value=io.BytesIO(b")]}'\n" + json.dumps(response).encode())) as opened:
                result = archive.query_omaha('windows', 'x86', channel)
                request = json.loads(opened.call_args.args[0].data)['request']
                self.assertNotIn('acceptformat', request)
                self.assertEqual(request['arch'], 'x86')
                self.assertEqual(request['os']['arch'], 'x86')
                self.assertEqual(request['app'][0]['ap'], '-arch_x86')
                self.assertEqual(request['app'][0]['release_channel'], channel)
                self.assertEqual(result.arch, 'x86')
                self.assertIn('windows-x86-' + channel, result.browser_asset)

    def test_x86_binary_is_checked_as_i386_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'chrome.exe'
            header = bytearray(128)
            header[:2] = b'MZ'
            struct.pack_into('<I', header, 0x3c, 0x40)
            header[0x40:0x44] = b'PE\0\0'
            struct.pack_into('<H', header, 0x44, 0x014c)
            binary.write_bytes(header)
            archive.validate_binary_arch(binary, 'windows', 'x86')
            for arch in ('x64', 'arm64'):
                with self.subTest(arch=arch), self.assertRaisesRegex(ValueError, 'PE architecture'):
                    archive.validate_binary_arch(binary, 'windows', arch)

    def test_x86_is_only_supported_for_windows(self):
        for platform in ('macos', 'linux'):
            with self.subTest(platform=platform), self.assertRaisesRegex(ValueError, 'Unsupported target'):
                offer(platform=platform, arch='x86')

    def test_only_repacked_browser_assets_are_selected(self):
        item = offer()
        self.assertEqual(item.asset_names, [item.browser_asset])
        self.assertNotEqual(item.browser_asset, offer(arch='arm64').browser_asset)
        raw = offer(platform='linux', original_name='google-chrome.deb')
        self.assertEqual(raw.asset_names, [raw.browser_asset])
        self.assertTrue(raw.browser_asset.endswith('.tar.xz'))
        self.assertTrue(item.browser_asset.endswith('.7z'))

    def test_tar_round_trip_preserves_unix_metadata(self):
        import tarfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'Chrome.app'
            source.mkdir()
            binary = source / 'binary'
            binary.write_bytes(b'original bytes' * 50)
            binary.chmod(0o755)
            (source / 'Current').symlink_to('binary')
            archive.compress_tar_xz(source, root / 'app.tar.xz')
            with tarfile.open(root / 'app.tar.xz') as result:
                self.assertEqual(result.getmember('Chrome.app/binary').mode, 0o755)
                self.assertTrue(result.getmember('Chrome.app/Current').issym())
                self.assertEqual(result.getmember('Chrome.app/Current').linkname, 'binary')

    def test_repair_helpers_are_packaged_beside_unmodified_app(self):
        import tarfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'Google Chrome.app'
            app.mkdir()
            (app / 'signed-content').write_bytes(b'unchanged app contents')
            repair = archive.stage_token_repair('macos', root)
            self.assertEqual(sorted(p.name for p in repair.iterdir()), ['README.md', 'fix-dm-token.sh'])
            archive.compress_tar_xz(app, root / 'browser.tar.xz', extras=(repair,))
            with tarfile.open(root / 'browser.tar.xz') as result:
                self.assertEqual(result.extractfile('Google Chrome.app/signed-content').read(), b'unchanged app contents')
                self.assertEqual(result.extractfile('chrome-dm-token/fix-dm-token.sh').read(),
                                 (repair / 'fix-dm-token.sh').read_bytes())
                self.assertEqual(result.getmember('chrome-dm-token/fix-dm-token.sh').mode, 0o755)
                self.assertFalse(any(name.startswith('Google Chrome.app/chrome-dm-token') for name in result.getnames()))

    def test_windows_compression_includes_repair_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            browser = root / 'Chrome-bin'
            browser.mkdir()
            repair = archive.stage_token_repair('windows', root)
            destination = root / 'browser.7z'
            def sevenzip(command, action, *args, **kwargs):
                if action == 'a':
                    self.assertIn('Chrome-bin', args)
                    self.assertIn('chrome-dm-token', args)
                    self.assertEqual(kwargs['cwd'], root)
                    destination.write_bytes(b'fixture archive')
            with patch.object(archive, 'seven_zip', side_effect=sevenzip) as run:
                archive.compress('7zz', browser, destination, extras=(repair,))
            self.assertEqual(run.call_args_list[-1].args[1], 't')

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

    def test_zip_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.zip'
            with zipfile.ZipFile(path, 'w') as package:
                package.writestr('../escape', b'x')
            with self.assertRaisesRegex(ValueError, 'Unsafe ZIP path'):
                archive.validate_zip(path)

    def test_macos_query_returns_standard_zip_and_rejects_crx_package(self):
        import io
        response = {'response': {'app': [{'appid': 'com.google.Chrome', 'status': 'ok',
            'updatecheck': {'status': 'ok', 'urls': {'url': [{'codebase': 'https://dl.google.com/releases/'}]},
            'manifest': {'version': '156.0.0.1', 'packages': {'package': [
                {'name': 'GoogleChrome-156.0.0.1.zip', 'size': 10, 'hash_sha256': 'a' * 64}]}}}}]}}
        with patch.object(archive.OPENER, 'open', side_effect=lambda *a, **k: io.BytesIO(json.dumps(response).encode())) as opened:
            result = archive.query_omaha('macos', 'arm64', 'stable')
            request = json.loads(opened.call_args.args[0].data)['request']
            self.assertNotIn('acceptformat', request)
            self.assertEqual(result.original_name, 'GoogleChrome-156.0.0.1.zip')
            response['response']['app'][0]['updatecheck']['manifest']['packages']['package'][0]['name'] = 'unexpected.crx3'
            with self.assertRaisesRegex(ValueError, 'Expected native .zip package'):
                archive.query_omaha('macos', 'arm64', 'stable')

    def test_zip_rejects_crx_wrapping(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wrapped.zip'
            path.write_bytes(struct.pack('<4sII', b'Cr24', 3, 0))
            with zipfile.ZipFile(path, 'a') as package:
                package.writestr('Google Chrome.app/test', b'data')
            with self.assertRaisesRegex(ValueError, 'Not a standard ZIP'):
                archive.validate_zip(path)

    def test_windows_build_uses_downloaded_exe_directly(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            item = offer(package_name='installer.exe', urls=('https://dl.google.com/installer.exe',))
            package = root / item.package_name
            package.write_bytes(b'downloaded EXE fixture')
            def extract(command, action, original, destination, *args):
                self.assertEqual((action, original), ('x', package))
                browser = Path(destination[2:]) / 'Chrome-bin'
                (browser / item.version).mkdir(parents=True)
                (browser / item.version / 'chrome.dll').write_bytes(b'dll')
                header = bytearray(128)
                header[:2] = b'MZ'
                struct.pack_into('<I', header, 0x3c, 0x40)
                header[0x40:0x44] = b'PE\0\0'
                struct.pack_into('<H', header, 0x44, 0x8664)
                (browser / 'chrome.exe').write_bytes(header)
            def compress(command, source, destination, extras=()):
                self.assertEqual([path.name for path in extras], ['chrome-dm-token'])
                self.assertTrue((extras[0] / 'fix-dm-token.ps1').is_file())
                destination.write_bytes(b'verified browser archive fixture')
            with patch.object(archive, 'verify_pe_version') as verify, \
                 patch.object(archive, 'seven_zip', side_effect=extract), patch.object(archive, 'compress', side_effect=compress):
                files = archive.build(item, package, root / 'dist', '7zz')
            verify.assert_called_once_with(package, item.version, '7zz')
            metadata = json.loads(files[1].read_text())
            self.assertEqual(metadata['source']['url'], item.urls[0])
            self.assertEqual(metadata['source']['format'], 'EXE')
            self.assertFalse(metadata['installer_executed'])

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

    def test_pe_version_is_checked_from_metadata_on_linux(self):
        import subprocess
        with patch.object(archive.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='FileVersion: 156.0.0.1\nProductVersion: 156.0.0.1\n')):
            archive.verify_pe_version(Path('installer.exe'), '156.0.0.1', '7zz')
            with self.assertRaisesRegex(ValueError, 'PE product version differs'):
                archive.verify_pe_version(Path('installer.exe'), '155.0.0.1', '7zz')

    def test_app_bundle_link_chains_and_executable_modes_are_preserved(self):
        import stat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'app.zip'
            with zipfile.ZipFile(path, 'w') as package:
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
    def setUp(self):
        patcher = patch.object(publish, 'query_omaha', return_value=None)
        self.native_query = patcher.start()
        self.addCleanup(patcher.stop)

    def test_native_inputs_need_no_second_query_and_round_trip_as_primary_links(self):
        items = [dict(schema_version=1, version='156.0.0.1', channel='stable', platform=platform, architecture='x64',
                      source={'url': f'https://dl.google.com/package.{suffix}'})
                 for platform, suffix in [('windows', 'exe'), ('macos', 'zip')]]
        enriched = publish.add_native_source_links(items)
        self.native_query.assert_not_called()
        _, body = publish.release_text('156.0.0.1', enriched)
        self.assertIn('[EXE](https://dl.google.com/package.exe)', body)
        self.assertIn('[ZIP](https://dl.google.com/package.zip)', body)
        self.assertNotIn('[CRX3]', body)
        self.assertNotIn('Legacy CRX3', body)
        release = dict(tag_name='156.0.0.1', body=body, assets=[{'name': archive.browser_asset_name(i['platform'], 'x64', 'stable', i['version'])} for i in items])
        restored = publish.existing_release_manifests(release)
        self.assertEqual({i['source']['url'] for i in restored}, {i['source']['url'] for i in items})

    def test_old_crx_and_exe_table_is_readable_and_prefers_native_link(self):
        version = '156.0.0.1'
        body = '| Stable | Windows | X64 | [CRX3](https://dl.google.com/old.crx3) · [EXE](https://dl.google.com/native.exe) |\n'
        release = dict(tag_name=version, body=body, assets=[{'name': archive.browser_asset_name('windows', 'x64', 'stable', version)}])
        restored = publish.existing_release_manifests(release)
        self.assertEqual(restored[0]['source']['url'], 'https://dl.google.com/old.crx3')
        _, updated = publish.release_text(version, restored)
        self.assertIn('[EXE](https://dl.google.com/native.exe)', updated)
        self.assertNotIn('[CRX3]', updated)

    def test_matching_native_links_are_rendered_and_preserved_without_requery(self):
        item = dict(schema_version=1, version='156.0.0.1', channel='stable', platform='windows', architecture='x64',
                    source={'url': 'https://dl.google.com/package.crx3'})
        self.native_query.return_value = offer(version=item['version'], package_name='156.0.0.1_chrome_installer_uncompressed.exe',
                                              urls=('https://dl.google.com/156.0.0.1_chrome_installer_uncompressed.exe',))
        enriched = publish.add_native_source_links([item, item])
        self.native_query.assert_called_once_with('windows', 'x64', 'stable', allow_no_update=True)
        _, body = publish.release_text(item['version'], enriched)
        self.assertIn('[EXE](https://dl.google.com/156.0.0.1_chrome_installer_uncompressed.exe)', body)
        self.assertNotIn('[CRX3]', body)
        restored = publish.existing_release_manifests(dict(tag_name=item['version'], body=body,
                    assets=[{'name': archive.browser_asset_name('windows', 'x64', 'stable', item['version'])}]))
        self.native_query.reset_mock()
        repeated = publish.add_native_source_links([*restored, item])
        self.native_query.assert_not_called()
        self.assertEqual(repeated[-1]['source']['native_url'], enriched[-1]['source']['native_url'])

    def test_native_version_mismatch_is_not_linked_and_mac_zip_round_trips(self):
        item = dict(schema_version=1, version='156.0.0.1', channel='extended', platform='macos', architecture='arm64',
                    source={'url': 'https://dl.google.com/mac.crx3'})
        self.native_query.return_value = offer(platform='macos', arch='arm64', channel='extended', version='155.0.0.1',
                                              package_name='GoogleChrome-155.0.0.1.zip', urls=('https://dl.google.com/old.zip',))
        missing = publish.add_native_source_links([item])
        self.assertNotIn('native_url', missing[0]['source'])
        self.assertNotIn('[ZIP]', publish.release_text(item['version'], missing)[1])
        self.assertIn('Legacy CRX3', publish.release_text(item['version'], missing)[1])
        matched = dict(item, source={**item['source'], 'native_url': 'https://dl.google.com/GoogleChrome-156.0.0.1.zip'})
        _, body = publish.release_text(item['version'], [matched])
        restored = publish.existing_release_manifests(dict(tag_name=item['version'], body=body,
                    assets=[{'name': archive.browser_asset_name('macos', 'arm64', 'extended', item['version'])}]))
        self.assertEqual(restored[0]['source']['native_url'], matched['source']['native_url'])

    def test_native_source_retries_the_archived_version_when_latest_differs(self):
        item = dict(schema_version=1, version='152.0.0.1', channel='extended', platform='windows', architecture='x86',
                    source={'url': 'https://dl.google.com/original.crx3'})
        latest = offer(arch='x86', channel='extended', version='156.0.0.1', package_name='156.0.0.1_chrome_installer_uncompressed.exe')
        exact = offer(arch='x86', channel='extended', version='152.0.0.1', package_name='152.0.0.1_chrome_installer_uncompressed.exe',
                      urls=('https://dl.google.com/152.0.0.1_chrome_installer_uncompressed.exe',))
        self.native_query.side_effect = [latest, exact]
        enriched = publish.add_native_source_links([item])
        self.native_query.assert_called_with('windows', 'x86', 'extended', allow_no_update=True, target_version=item['version'])
        self.assertEqual(enriched[0]['source']['native_url'], exact.urls[0])

    def test_only_currently_discovered_versions_are_refreshed(self):
        version = '152.0.0.1'
        item = dict(schema_version=1, version=version, channel='stable', platform='windows', architecture='x64',
                    source={'url': f'https://dl.google.com/{version}.crx3'})
        title, body = publish.release_text(version, [item])
        release = dict(id=1, tag_name=version, name='Old title', draft=True,
                       body=body.replace('[CRX3]', '[Google original]') + '\nOld custom description.\n',
                       assets=[{'name': archive.browser_asset_name('windows', 'x64', 'stable', version)}])
        with patch.object(publish, 'get_release', return_value=release) as get, patch.object(publish, 'gh') as api:
            publish.refresh_release_descriptions('owner/repo', [version, version])
        get.assert_called_once_with('owner/repo', version)
        api.assert_called_once()
        self.assertEqual(api.call_args.args[:2], ('api', 'repos/owner/repo/releases/1'))
        payload = api.call_args.kwargs['data']
        self.assertEqual(set(payload), {'name', 'body'})
        self.assertIn('[CRX3]', payload['body'])
        self.assertNotIn('Old custom description.', payload['body'])
        self.assertEqual(payload['name'], title)

    def test_unchanged_current_description_is_checked_without_redundant_patch(self):
        item = dict(schema_version=1, version='152.0.0.1', channel='stable', platform='windows', architecture='x64',
                    source={'url': 'https://dl.google.com/source.crx3', 'native_url': 'https://dl.google.com/source.exe'})
        title, body = publish.release_text(item['version'], [item])
        release = dict(id=1, tag_name=item['version'], name=title, body=body,
                       assets=[{'name': archive.browser_asset_name('windows', 'x64', 'stable', item['version'])}])
        with patch.object(publish, 'get_release', return_value=release) as get, patch.object(publish, 'gh') as api:
            publish.refresh_release_descriptions('owner/repo', {item['version']})
        get.assert_called_once_with('owner/repo', item['version'])
        api.assert_not_called()

    def test_description_refresh_refuses_to_silently_drop_unrecognized_coverage(self):
        release = dict(id=1, tag_name='152.0.0.1', name='Old title', body='No source coverage',
                       assets=[{'name': archive.browser_asset_name('windows', 'x64', 'stable', '152.0.0.1')}])
        with patch.object(publish, 'get_release', return_value=release), patch.object(publish, 'gh') as api:
            with self.assertRaisesRegex(ValueError, 'Cannot recover source coverage'):
                publish.refresh_release_descriptions('owner/repo', {'152.0.0.1'})
        api.assert_not_called()

    def test_no_current_versions_means_no_release_requests(self):
        with patch.object(publish, 'get_release') as get, patch.object(publish, 'gh') as api:
            publish.refresh_release_descriptions('owner/repo', set())
        get.assert_not_called()
        api.assert_not_called()

    def test_cli_uses_discovery_even_without_new_archives(self):
        with patch.object(sys, 'argv', ['publish_releases.py', '--repository', 'owner/repo', '--target', 'commit']), \
             patch.object(publish, 'publish', return_value=set()) as archives, \
             patch.object(publish, 'discovered_versions', return_value={'156.0.0.1'}) as discovery, \
             patch.object(publish, 'refresh_release_descriptions') as refresh, patch.object(publish, 'log_runtime'):
            publish.main()
        archives.assert_called_once_with('owner/repo', 'commit', Path('dist'))
        discovery.assert_called_once_with(Path('dist'))
        refresh.assert_called_once_with('owner/repo', {'156.0.0.1'})

    def test_cli_also_refreshes_versions_built_without_discovery_files(self):
        with patch.object(sys, 'argv', ['publish_releases.py', '--repository', 'owner/repo', '--target', 'commit']), \
             patch.object(publish, 'publish', return_value={'156.0.0.2'}), \
             patch.object(publish, 'discovered_versions', return_value={'156.0.0.1'}), \
             patch.object(publish, 'refresh_release_descriptions') as refresh, patch.object(publish, 'log_runtime'):
            publish.main()
        refresh.assert_called_once_with('owner/repo', {'156.0.0.1', '156.0.0.2'})

    def test_linux_rpm_links_match_version_and_survive_incremental_release(self):
        version = '156.0.0.1'
        item = dict(schema_version=1, version=version, channel='stable', platform='linux', architecture='arm64',
                    source={'url': 'https://dl.google.com/chrome_156.0.0.1_arm64.deb'})
        rpm = 'https://dl.google.com/google-chrome-stable-156.0.0.1-1.aarch64.rpm'
        with patch.object(publish, 'rpm_sources', return_value={version: rpm}):
            enriched = publish.add_linux_rpm_links([item])
        _, body = publish.release_text(version, enriched)
        self.assertIn('[DEB](' + item['source']['url'] + ') · [RPM](' + rpm + ')', body)
        restored = publish.existing_release_manifests(dict(tag_name=version, body=body,
                    assets=[{'name': archive.browser_asset_name('linux', 'arm64', 'stable', version)}]))
        self.assertEqual(restored[0]['source']['rpm_url'], rpm)
        with patch.object(publish, 'rpm_sources', side_effect=AssertionError('Existing provenance must be retained')):
            repeated = publish.add_linux_rpm_links([*restored, item])
        self.assertEqual(repeated[-1]['source']['rpm_url'], rpm)
        with patch.object(publish, 'rpm_sources', return_value={'157.0.0.1': rpm}):
            missing = publish.add_linux_rpm_links([item])
        self.assertNotIn('rpm_url', missing[0]['source'])
        self.assertNotIn('[RPM]', publish.release_text(version, missing)[1])

    def test_only_browser_archive_is_uploaded_even_with_legacy_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / '156.0.0.1'
            folder.mkdir()
            name = archive.browser_asset_name('windows', 'x86', 'stable', folder.name)
            browser = folder / name
            browser.write_bytes(b'browser archive')
            item = dict(schema_version=1, version=folder.name, platform='windows', architecture='x86',
                        channel='stable', source={'url': 'https://dl.google.com/payload.crx3'},
                        files=[{'name': name, 'size': browser.stat().st_size, 'sha256': archive.sha256(browser)},
                               {'name': 'original.exe.xz', 'size': 10, 'sha256': '0' * 64}])
            (folder / 'internal-manifest.json').write_text(json.dumps(item))
            groups = publish.local_groups(root)
            self.assertEqual(set(groups[folder.name]['assets']), {name})
            with patch.object(publish, 'get_release', return_value=None), patch.object(publish, 'gh', return_value={'id': 1, 'assets': []}) as api, patch.object(publish.subprocess, 'run') as upload:
                publish.publish('owner/repo', 'commit', root)
                upload.assert_called_once_with(['gh', 'release', 'upload', folder.name, str(browser), '--repo', 'owner/repo'], check=True)
                self.assertIn('https://dl.google.com/payload.crx3', api.call_args.kwargs['data']['body'])

    def test_existing_coverage_and_source_links_survive_without_metadata_assets(self):
        items = [dict(schema_version=1, version='156.0.0.1', platform='windows', architecture='x86', channel='stable', source={'url': 'https://dl.google.com/windows.crx3'}),
                 dict(schema_version=1, version='156.0.0.1', platform='macos', architecture='arm64', channel='extended', source={'url': 'https://dl.google.com/mac.crx3'})]
        _, body = publish.release_text('156.0.0.1', items)
        name = archive.browser_asset_name('windows', 'x86', 'stable', '156.0.0.1')
        restored = publish.existing_release_manifests({'tag_name': '156.0.0.1', 'body': body, 'assets': [{'name': name}]})
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['source']['url'], 'https://dl.google.com/windows.crx3')
        self.assertEqual(restored[0]['architecture'], 'x86')
        legacy = publish.existing_release_manifests({'tag_name': '156.0.0.1',
                    'body': body.replace('[CRX3]', '[Google original]'), 'assets': [{'name': name}]})
        self.assertEqual(legacy, restored)

    def test_release_links_to_the_actual_official_source_and_deduplicates_rows(self):
        item = dict(schema_version=1, version='156.0.0.1', channel='stable', platform='windows', architecture='x86',
                    source={'url': 'https://dl.google.com/release2/chrome/version/payload.crx3'})
        _, body = publish.release_text(item['version'], [item, item])
        self.assertIn('[CRX3](https://dl.google.com/release2/chrome/version/payload.crx3)', body)
        self.assertEqual(body.count('| Stable | Windows | X86 |'), 1)
        with self.assertRaises(ValueError):
            publish.release_text(item['version'], [dict(item, source={'url': 'https://evil.example/download'})])

    def test_release_accepts_windows_x86_and_lists_its_architecture(self):
        item = dict(schema_version=1, version='156.0.0.1', channel='stable', platform='windows', architecture='x86')
        publish.validate_manifest(item, item['version'])
        title, body = publish.release_text(item['version'], [item])
        self.assertEqual(title, '156.0.0.1 · Stable')
        self.assertIn('| Stable | Windows | X86 |', body)
        for platform in ('macos', 'linux'):
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                publish.validate_manifest(dict(item, platform=platform), item['version'])

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
