"""Exercise channel sharing through discovery, release text and publication."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import chrome_archive as archive
import publish_releases as publish
from release_metadata import compact_manifest, manifest_asset_name, metadata_comment


VERSION = '156.0.0.1'


def offer(channel='stable', digest='a' * 64, size=10, version=VERSION, url='https://dl.google.com/browser.exe'):
    return archive.Offer('windows', 'x64', channel, version, 'browser.exe', size, digest, (url,), 'browser.exe')


def manifest(item, name=None):
    return dict(schema_version=1, version=item.version, platform=item.platform, architecture=item.arch,
                channel=item.channel, browser_asset=name or item.browser_asset,
                source=dict(url=item.urls[0], sha256=item.digest, size=item.size))


class ChannelDedupTests(unittest.TestCase):
    def run_archive(self, root, offers, release=None):
        def build(item, package, output, command):
            folder = output / item.version
            folder.mkdir(parents=True, exist_ok=True)
            browser = folder / item.browser_asset
            browser.write_bytes(item.digest.encode())
            value = manifest(item)
            value['files'] = [dict(name=browser.name, size=browser.stat().st_size, sha256=archive.sha256(browser))]
            record = folder / item.manifest_name
            archive.write_json(record, value)
            return [browser, record]
        with patch.object(sys, 'argv', ['chrome_archive.py', '--platform', 'windows', '--arch', 'x64',
                         '--output', str(root), '--repository', 'owner/repo']), \
             patch.object(archive.shutil, 'which', return_value='/existing/7zz'), \
             patch.object(archive, 'query_omaha', side_effect=offers) as query, \
             patch.object(archive, 'get_release', return_value=release), \
             patch.object(archive, 'download', return_value=root / 'package') as download, \
             patch.object(archive, 'build', side_effect=build) as built, patch.object(archive, 'log_runtime'):
            archive.main()
        return query, download, built

    def release(self, records, assets=None):
        title, body = publish.release_text(VERSION, records)
        if assets is None:
            assets = [{'name': name} for name in {manifest_asset_name(item) for item in records}]
        return dict(id=1, tag_name=VERSION, name=title, body=body, assets=assets)

    def test_identical_channels_build_once_publish_once_and_reuse_on_next_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            queries, downloads, builds = self.run_archive(root, [offer(), offer('extended')])
            self.assertEqual(queries.call_count, 2)
            self.assertEqual(downloads.call_count, 1)
            self.assertEqual(builds.call_count, 1)
            group = publish.local_groups(root)[VERSION]
            self.assertEqual(len(group['assets']), 1)
            self.assertEqual({item['channel'] for item in group['manifests']}, {'stable', 'extended'})
            with patch.object(publish, 'get_release', return_value=None), \
                 patch.object(publish, 'gh', return_value=dict(id=1, assets=[])) as api, \
                 patch.object(publish.subprocess, 'run') as upload:
                publish.publish('owner/repo', 'commit', root)
            self.assertEqual(upload.call_count, 1)
            body = api.call_args.kwargs['data']['body']
            self.assertEqual(body.count('| Stable / Extended Stable | Windows | X64 |'), 1)
            release = self.release(group['manifests'])
            self.assertEqual(release['body'], body)
            recovered = publish.existing_release_manifests(release)
            self.assertEqual({item['channel'] for item in recovered}, {'stable', 'extended'})
            self.assertEqual({item['source']['sha256'] for item in recovered}, {'a' * 64})
            next_root = root / 'next-run'
            queries, downloads, builds = self.run_archive(next_root, [offer(), offer('extended')], release)
            self.assertEqual(queries.call_count, 2)
            downloads.assert_not_called()
            builds.assert_not_called()
            group = publish.local_groups(next_root)[VERSION]
            self.assertFalse(group['assets'])
            with patch.object(publish, 'get_release', return_value=release), \
                 patch.object(publish, 'gh') as api, patch.object(publish.subprocess, 'run') as upload:
                publish.publish('owner/repo', 'commit', next_root)
                publish.refresh_release_descriptions('owner/repo', {VERSION})
            upload.assert_not_called()
            api.assert_not_called()

    def test_same_version_different_packages_get_distinct_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, downloads, builds = self.run_archive(root, [offer(), offer('extended', digest='b' * 64)])
            self.assertEqual(downloads.call_count, 2)
            self.assertEqual(builds.call_count, 2)
            group = publish.local_groups(root)[VERSION]
            self.assertEqual(len(group['assets']), 2)
            self.assertIn(offer().browser_asset, group['assets'])
            self.assertIn(archive.browser_asset_name('windows', 'x64', None, VERSION, 'b' * 64), group['assets'])
            recovered = publish.existing_release_manifests(self.release(group['manifests']))
            self.assertEqual(len({item['browser_asset'] for item in recovered}), 2)

    def test_same_hash_different_size_does_not_share(self):
        with tempfile.TemporaryDirectory() as directory:
            _, _, builds = self.run_archive(Path(directory), [offer(), offer('extended', size=11)])
            self.assertEqual(builds.call_count, 2)

    def test_same_package_name_with_different_content_has_separate_cache_entries(self):
        payloads = [b'first payload', b'another payload']
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for payload in payloads:
                item = offer(digest=hashlib.sha256(payload).hexdigest(), size=len(payload))
                with patch.object(archive.OPENER, 'open', return_value=io.BytesIO(payload)):
                    paths.append(archive.download(item, Path(directory)))
            self.assertNotEqual(paths[0], paths[1])
            self.assertEqual([path.read_bytes() for path in paths], payloads)

    def test_different_versions_remain_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, builds = self.run_archive(root, [offer(), offer('extended', version='152.0.0.1')])
            self.assertEqual(builds.call_count, 2)
            self.assertEqual(set(publish.local_groups(root)), {VERSION, '152.0.0.1'})

    def test_different_source_urls_with_identical_hash_share_archive_keep_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, builds = self.run_archive(root, [offer(), offer('extended', url='https://dl.google.com/extended.exe')])
            self.assertEqual(builds.call_count, 1)
            records = publish.local_groups(root)[VERSION]['manifests']
            release = self.release(records)
            self.assertIn('[EXE](https://dl.google.com/extended.exe)', release['body'])
            self.assertIn('[EXE](https://dl.google.com/browser.exe)', release['body'])
            self.assertEqual(len({item['browser_asset'] for item in publish.existing_release_manifests(release)}), 1)

    def test_incremental_new_channel_reuses_verified_remote_source(self):
        record = manifest(offer())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, downloads, builds = self.run_archive(root, [offer(), offer('extended')], self.release([record]))
            downloads.assert_not_called()
            builds.assert_not_called()
            self.assertEqual({item['channel'] for item in publish.local_groups(root)[VERSION]['manifests']}, {'stable', 'extended'})

    def test_incremental_different_source_preserves_old_asset_mapping(self):
        old = manifest(offer())
        release = self.release([old])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, builds = self.run_archive(root, [offer(), offer('extended', digest='b' * 64)], release)
            self.assertEqual(builds.call_count, 1)
            with patch.object(publish, 'get_release', return_value=release), \
                 patch.object(publish, 'gh') as api, patch.object(publish.subprocess, 'run'):
                publish.publish('owner/repo', 'commit', root)
            body = api.call_args.kwargs['data']['body']
            names = [old['browser_asset'], builds.call_args.args[0].browser_asset]
            recovered = publish.existing_release_manifests(dict(tag_name=VERSION, body=body, assets=[dict(name=n) for n in names]))
            self.assertEqual({item['browser_asset'] for item in recovered}, set(names))

    def test_legacy_merged_table_recovers_both_channel_names(self):
        body = '| Stable / Extended Stable | Windows | X64 | [EXE](https://dl.google.com/browser.exe) |\n'
        assets = [dict(name=archive.browser_asset_name('windows', 'x64', channel, VERSION)) for channel in archive.CHANNELS]
        records = publish.existing_release_manifests(dict(tag_name=VERSION, body=body, assets=assets))
        self.assertEqual({item['channel'] for item in records}, set(archive.CHANNELS))

    def test_publisher_reuses_legacy_asset_by_archive_digest_without_deleting(self):
        legacy = archive.browser_asset_name('windows', 'x64', 'stable', VERSION)
        old = manifest(offer(), legacy)
        old['source'].pop('sha256')
        old['source'].pop('size')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_archive(root, [offer(), offer('extended')])
            group = publish.local_groups(root)[VERSION]
            path = next(iter(group['assets'].values()))
            release = self.release([old], [dict(name=legacy, size=path.stat().st_size, digest='sha256:' + archive.sha256(path))])
            with patch.object(publish, 'get_release', return_value=release), \
                 patch.object(publish, 'gh') as api, patch.object(publish.subprocess, 'run') as upload:
                publish.publish('owner/repo', 'commit', root)
            upload.assert_not_called()
            self.assertEqual(api.call_count, 1)
            release['body'] = api.call_args.kwargs['data']['body']
            records = publish.existing_release_manifests(release)
            self.assertEqual({item['browser_asset'] for item in records}, {legacy})
            self.assertEqual({item['channel'] for item in records}, set(archive.CHANNELS))

    def test_reuse_record_cannot_publish_missing_archive(self):
        group = dict(manifests=[manifest(offer())], assets={})
        with self.assertRaisesRegex(ValueError, 'missing'):
            publish.deduplicate_assets(group, [], None)

    def test_both_legacy_assets_keep_provenance_after_sharing_and_refresh(self):
        records = [manifest(offer(channel), archive.browser_asset_name('windows', 'x64', channel, VERSION))
                   for channel in archive.CHANNELS]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_archive(root, [offer(), offer('extended')])
            group = publish.local_groups(root)[VERSION]
            path = next(iter(group['assets'].values()))
            assets = [dict(name=item['browser_asset'], size=path.stat().st_size, digest='sha256:' + archive.sha256(path))
                      for item in records]
            release = self.release(records, assets)
            with patch.object(publish, 'get_release', return_value=release), \
                 patch.object(publish, 'gh') as api, patch.object(publish.subprocess, 'run') as upload:
                publish.publish('owner/repo', 'commit', root)
                release['body'] = api.call_args.kwargs['data']['body']
                publish.refresh_release_descriptions('owner/repo', {VERSION})
            upload.assert_not_called()
            recovered = publish.existing_release_manifests(release)
            self.assertEqual({item['browser_asset'] for item in recovered}, {item['browser_asset'] for item in records})

    def test_different_packages_with_identical_repacked_bytes_share_attachment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_archive(root, [offer(), offer('extended', digest='b' * 64)])
            group = publish.local_groups(root)[VERSION]
            paths = list(group['assets'].values())
            paths[1].write_bytes(paths[0].read_bytes())
            result = publish.deduplicate_assets(group, [], None)
            self.assertEqual(len(result['assets']), 1)
            self.assertEqual(len({item['browser_asset'] for item in result['manifests']}), 1)
            self.assertEqual({item['source']['sha256'] for item in result['manifests']}, {'a' * 64, 'b' * 64})

    def test_remote_asset_without_digest_is_not_used_for_content_dedup(self):
        legacy = archive.browser_asset_name('windows', 'x64', 'stable', VERSION)
        record = manifest(offer(), legacy)
        record['source'].pop('sha256')
        record['source'].pop('size')
        release = self.release([record])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.run_archive(root, [offer(), offer('extended')])
            group = publish.local_groups(root)[VERSION]
            result = publish.deduplicate_assets(group, [record], release)
            self.assertEqual(len(result['assets']), 1)
            self.assertNotIn(legacy, {item['browser_asset'] for item in result['manifests']})

    def test_identical_universal_mac_archives_keep_both_architecture_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records, assets = [], {}
            for arch in ('x64', 'arm64'):
                name = archive.browser_asset_name('macos', arch, None, VERSION)
                record = dict(manifest(offer()), platform='macos', architecture=arch, browser_asset=name,
                              source={'url': 'https://dl.google.com/browser.zip', 'sha256': 'a' * 64, 'size': 10})
                path = root / name
                path.write_bytes(b'identical universal archive')
                records.append(record)
                assets[name] = path
            group = publish.deduplicate_assets(dict(manifests=records, assets=assets), [], None)
            self.assertEqual(len(group['assets']), 2)
            recovered = publish.existing_release_manifests(self.release(group['manifests']))
            self.assertEqual({item['architecture'] for item in recovered}, {'x64', 'arm64'})

    def test_damaged_metadata_does_not_fall_back_to_legacy_table(self):
        body = '| Stable | Windows | X64 | [EXE](https://dl.google.com/browser.exe) |\n'
        body += '<!-- chrome-archive-metadata-v1\ninvalid JSON\n-->'
        release = dict(tag_name=VERSION, body=body,
                       assets=[dict(name=archive.browser_asset_name('windows', 'x64', 'stable', VERSION))])
        with self.assertRaises(ValueError):
            publish.existing_release_manifests(release)

    def test_discovery_cannot_claim_another_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive.write_json(root / 'windows-discovery.json', dict(schema_version=1, platform='windows',
                               architecture='arm64', channels={'stable': VERSION}, manifests=[manifest(offer())]))
            with self.assertRaisesRegex(ValueError, 'does not match discovery'):
                publish.local_groups(root)

    def test_metadata_rejects_wrong_version_unsafe_names_and_non_google_sources(self):
        for changes in [dict(version='155.0.0.1'), dict(browser_asset='../browser.7z'),
                        dict(source={'url': 'https://evil.example/browser.exe'})]:
            value = dict(manifest(offer()), **changes)
            body = '<!-- chrome-archive-metadata-v1\n' + json.dumps([value]) + '\n-->'
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                publish.existing_release_manifests(dict(tag_name=VERSION, body=body, assets=[dict(name=value['browser_asset'])]))

    def test_metadata_omits_unneeded_fields_and_escapes_html_comment(self):
        value = manifest(offer(url='https://dl.google.com/browser.exe?x=-->'))
        value['source']['arguments_not_executed'] = 'unnecessary installer arguments'
        value['unrelated'] = 'not a durable source fact'
        body = metadata_comment([value])
        self.assertEqual(body.count('-->'), 1)
        self.assertNotIn('unnecessary installer arguments', body)
        self.assertNotIn('not a durable source fact', body)
        records = publish.existing_release_manifests(dict(tag_name=VERSION, body=body, assets=[dict(name=value['browser_asset'])]))
        self.assertEqual(records, [compact_manifest(value)])


if __name__ == '__main__':
    unittest.main()
