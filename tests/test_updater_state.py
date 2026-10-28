"""Exercise persisted update state across querying, publication and the README."""
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
import update_readme as readme
import updater_state as state

OLD = '156.0.8078.48'
NEW = '156.0.8078.74'
KEY = 'windows/x64/extended'


def response(cohort=None):
    return {'response': {'app': [{'appid': '{8A69D345-D564-463C-AFF1-A69D9E530F96}',
                                 'status': 'ok', 'updatecheck': {'status': 'noupdate'},
                                 **(cohort or {})}]}}


def discovery(root, entry):
    archive.write_json(root / 'chrome-windows-x64-discovery.json', {
        'schema_version': 1, 'platform': 'windows', 'architecture': 'x64',
        'channels': {'extended': entry['version']}, 'manifests': [],
        'updater_state': {'extended': entry},
    })


def release(version=NEW):
    record = {'schema_version': 1, 'platform': 'windows', 'architecture': 'x64',
              'channel': 'extended', 'version': version,
              'browser_asset': archive.browser_asset_name('windows', 'x64', None, version),
              'source': {'url': 'https://dl.google.com/browser.exe', 'size': 10, 'sha256': 'a' * 64}}
    title, body = publish.release_text(version, [record])
    return {'id': 1, 'tag_name': version, 'name': title, 'body': body, 'draft': False,
            'assets': [{'name': record['browser_asset']}]}


class UpdaterStateTests(unittest.TestCase):
    def test_current_version_and_cohort_are_sent_and_noupdate_keeps_version(self):
        entry = {'version': NEW, 'cohort': 'old-group', 'cohorthint': 'keep', 'cohortname': 'old-name'}
        answer = response(cohort={'cohort': 'new-group', 'cohortname': ''})
        with patch.object(archive.OPENER, 'open', return_value=io.BytesIO(json.dumps(answer).encode())) as opened:
            self.assertIsNone(archive.query_omaha('windows', 'x64', 'extended', state=entry, allow_no_update=True))
        app = json.loads(opened.call_args.args[0].data)['request']['app'][0]
        self.assertEqual(app['version'], NEW)
        self.assertEqual(app['cohort'], 'old-group')
        self.assertEqual(app['cohorthint'], 'keep')
        self.assertFalse(app['updatecheck']['sameversionupdate'])
        self.assertNotIn('rollback_allowed', app['updatecheck'])
        self.assertEqual(entry, {'version': NEW, 'cohort': 'new-group', 'cohorthint': 'keep', 'cohortname': ''})

    def test_error_response_does_not_replace_cohort(self):
        entry = {'version': NEW, 'cohort': 'keep'}
        answer = response(cohort={'cohort': 'discard'})
        answer['response']['app'][0]['updatecheck']['status'] = 'error-internal'
        with patch.object(archive.OPENER, 'open', return_value=io.BytesIO(json.dumps(answer).encode())):
            with self.assertRaisesRegex(RuntimeError, 'error-internal'):
                archive.query_omaha('windows', 'x64', 'extended', state=entry, allow_no_update=True)
        self.assertEqual(entry, {'version': NEW, 'cohort': 'keep'})

    def test_invalid_state_and_private_fields_are_rejected(self):
        for key, entry in [
            ('linux/x64/extended', {'version': NEW}),
            ('macos/x86/stable', {'version': NEW}),
            ('windows/x64', {'version': NEW}),
            (KEY, {'version': '156'}),
            (KEY, {'version': NEW, 'token': 'not-allowed'}),
            (KEY, {'version': NEW, 'cohort': 'bad\nvalue'}),
            (KEY, {'version': NEW, 'cohortname': 123}),
        ]:
            with self.subTest(key=key, entry=entry), self.assertRaises(ValueError):
                state.validate_entry(key, entry)

    def test_noupdate_round_trip_isolates_channels_and_leaves_input_state_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            initial = {KEY: {'version': NEW, 'cohort': 'extended-group'},
                       'windows/x64/stable': {'version': OLD, 'cohort': 'stable-group'}}
            state.write_state(path, initial)
            original = path.read_bytes()
            requests = []
            def open_request(request, **kwargs):
                app = json.loads(request.data)['request']['app'][0]
                requests.append(app)
                return io.BytesIO(json.dumps(response(cohort={'cohort': app['cohort'] + '-next'})).encode())
            with patch.object(sys, 'argv', ['chrome_archive.py', '--platform', 'windows', '--arch', 'x64',
                                          '--output', str(root), '--state', str(path)]), \
                 patch.object(archive.shutil, 'which', return_value='/existing/7zz'), \
                 patch.object(archive.OPENER, 'open', side_effect=open_request), \
                 patch.object(archive, 'download') as download, patch.object(archive, 'build') as build, \
                 patch.object(archive, 'log_runtime'):
                archive.main()
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual([(app['release_channel'], app['version'], app['cohort']) for app in requests],
                             [('stable', OLD, 'stable-group'), ('extended', NEW, 'extended-group')])
            download.assert_not_called()
            build.assert_not_called()
            result = json.loads((root / 'chrome-windows-x64-discovery.json').read_text())
            self.assertEqual(result['channels'], {'stable': OLD, 'extended': NEW})
            self.assertEqual(result['updater_state']['extended'], {'version': NEW, 'cohort': 'extended-group-next'})

    def test_lower_offer_is_not_downloaded_or_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            state.write_state(path, {KEY: {'version': NEW}})
            offered = archive.Offer('windows', 'x64', 'extended', OLD, 'browser.exe', 10,
                                    'a' * 64, ('https://dl.google.com/browser.exe',), 'browser.exe')
            with patch.object(sys, 'argv', ['chrome_archive.py', '--platform', 'windows', '--arch', 'x64',
                                          '--channel', 'extended', '--output', str(root), '--state', str(path)]), \
                 patch.object(archive.shutil, 'which', return_value='/existing/7zz'), \
                 patch.object(archive, 'query_omaha', return_value=offered), \
                 patch.object(archive, 'download') as download, patch.object(archive, 'build') as build, \
                 patch.object(archive, 'log_runtime'):
                archive.main()
            result = state.merge_discovery(state.read_state(path), root)
            self.assertEqual(result[KEY]['version'], NEW)
            download.assert_not_called()
            build.assert_not_called()

    def test_stale_candidate_does_not_replace_a_newer_version_or_its_cohort(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = {KEY: {'version': NEW, 'cohort': 'keep'},
                       'macos/x64/stable': {'version': OLD}}
            discovery(root, {'version': OLD, 'cohort': 'stale'})
            self.assertEqual(state.merge_discovery(initial, root), initial)
            discovery(root, {'version': NEW, 'cohort': ''})
            merged = state.merge_discovery(initial, root)
            self.assertEqual(merged[KEY], {'version': NEW, 'cohort': ''})
            self.assertEqual(merged['macos/x64/stable'], initial['macos/x64/stable'])

    def test_state_candidate_must_match_discovery_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            discovery(root, {'version': NEW})
            path = root / 'chrome-windows-x64-discovery.json'
            value = json.loads(path.read_text())
            value['channels']['extended'] = OLD
            archive.write_json(path, value)
            with self.assertRaisesRegex(ValueError, 'does not match discovery'):
                state.merge_discovery({}, root)

    def test_state_advances_only_after_publish_refresh_and_asset_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            state.write_state(path, {KEY: {'version': OLD}})
            original = path.read_bytes()
            discovery(root, {'version': NEW, 'cohort': 'next'})
            def published(*args):
                self.assertEqual(path.read_bytes(), original)
                return {NEW}
            def refreshed(*args):
                self.assertEqual(path.read_bytes(), original)
            with patch.object(sys, 'argv', ['publish_releases.py', '--input', str(root), '--state', str(path),
                                          '--repository', 'owner/repo', '--target', 'commit']), \
                 patch.object(publish, 'publish', side_effect=published), \
                 patch.object(publish, 'refresh_release_descriptions', side_effect=refreshed), \
                 patch.object(publish, 'get_release', return_value=release()), patch.object(publish, 'log_runtime'):
                publish.main()
            self.assertEqual(state.read_state(path), {KEY: {'version': NEW, 'cohort': 'next'}})
            # The next process must consume exactly what publication persisted.
            with patch.object(sys, 'argv', ['chrome_archive.py', '--platform', 'windows', '--arch', 'x64',
                                          '--channel', 'extended', '--output', str(root), '--state', str(path)]), \
                 patch.object(archive.shutil, 'which', return_value='/existing/7zz'), \
                 patch.object(archive.OPENER, 'open', return_value=io.BytesIO(json.dumps(response()).encode())) as opened, \
                 patch.object(archive, 'build') as build, patch.object(archive, 'log_runtime'):
                archive.main()
            app = json.loads(opened.call_args.args[0].data)['request']['app'][0]
            self.assertEqual((app['version'], app['cohort']), (NEW, 'next'))
            build.assert_not_called()

    def test_publication_failures_do_not_advance_state(self):
        for failure in ('publish', 'refresh', 'missing_release', 'draft', 'missing_asset'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                path = root / 'state.json'
                state.write_state(path, {KEY: {'version': OLD}})
                original = path.read_bytes()
                discovery(root, {'version': NEW, 'cohort': 'next'})
                remote = release()
                if failure == 'draft':
                    remote['draft'] = True
                if failure == 'missing_asset':
                    remote['assets'] = []
                with patch.object(sys, 'argv', ['publish_releases.py', '--input', str(root), '--state', str(path),
                                              '--repository', 'owner/repo', '--target', 'commit']), \
                     patch.object(publish, 'publish', side_effect=RuntimeError('upload failed') if failure == 'publish' else None,
                                  return_value={NEW}), \
                     patch.object(publish, 'refresh_release_descriptions',
                                  side_effect=RuntimeError('refresh failed') if failure == 'refresh' else None), \
                     patch.object(publish, 'get_release', return_value=None if failure == 'missing_release' else remote), \
                     patch.object(publish, 'log_runtime'):
                    with self.assertRaises((RuntimeError, ValueError)):
                        publish.main()
                self.assertEqual(path.read_bytes(), original)

    def test_preview_never_updates_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            state.write_state(path, {KEY: {'version': OLD}})
            original = path.read_bytes()
            discovery(root, {'version': NEW})
            with patch.object(sys, 'argv', ['publish_releases.py', '--input', str(root), '--state', str(path),
                                          '--repository', 'owner/repo', '--preview']), \
                 patch.object(publish, 'get_release') as get, patch.object(publish, 'log_runtime'):
                publish.main()
            self.assertEqual(path.read_bytes(), original)
            get.assert_not_called()

    def test_readme_uses_published_json_not_unpublished_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'state.json'
            target = root / 'README.md'
            state.write_state(path, {KEY: {'version': OLD}})
            discovery(root, {'version': NEW})
            table = readme.release_table('owner/repo', {('windows', 'x64'): {'extended': NEW}})
            target.write_text(f'{readme.START}\n{table}\n{readme.END}\n')
            with patch.object(sys, 'argv', ['update_readme.py', '--input', str(root), '--state', str(path),
                                          '--readme', str(target), '--repository', 'owner/repo']):
                readme.main()
            self.assertEqual(readme.parse_table(target.read_text(), 'owner/repo'),
                             {('windows', 'x64'): {'extended': OLD}})


if __name__ == '__main__':
    unittest.main()
