"""Check the README release table generated from discovery records."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_readme as readme


class UpdateReadmeTests(unittest.TestCase):
    def discovery(self, root, platform, arch, channels):
        (root / f'chrome-{platform}-{arch}-discovery.json').write_text(json.dumps(
            {'schema_version': 1, 'platform': platform, 'architecture': arch,
             'channels': channels, 'manifests': []}), encoding='utf-8')

    def test_table_links_every_target_and_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.discovery(root, 'windows', 'x64', {'stable': '156.0.1.2', 'extended': '152.0.3.4'})
            self.discovery(root, 'linux', 'arm64', {'stable': '156.0.1.3'})
            table = readme.release_table('o/r', readme.read_discovery(root))
        self.assertIn('| Windows | X64 | [156.0.1.2](https://github.com/o/r/releases/tag/156.0.1.2) | '
                      '[152.0.3.4](https://github.com/o/r/releases/tag/152.0.3.4) |', table)
        self.assertIn('| Linux | ARM64 | [156.0.1.3](https://github.com/o/r/releases/tag/156.0.1.3) | 官方不提供 |', table)
        self.assertIn('| Windows | X86 | — | — |', table)
        self.assertEqual(len(table.splitlines()), 2 + 7)

    def test_partial_run_keeps_existing_rows_and_replaces_only_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.discovery(root, 'macos', 'x64', {'stable': '156.0.1.4'})
            path = root / 'README.md'
            head = '# Chrome installer archive\n\n## 下载\n\n'
            tail = '\n\n## 其他\n'
            table = readme.release_table('o/r', {('macos', 'arm64'): {'stable': '155.0.0.1'}})
            path.write_text(f'{head}{readme.START}\n{table}\n{readme.END}{tail}', encoding='utf-8')
            sys.argv = ['update_readme.py', '--input', str(root), '--readme', str(path), '--repository', 'o/r']
            readme.main()
            text = path.read_text(encoding='utf-8')
        self.assertIn('[155.0.0.1](https://github.com/o/r/releases/tag/155.0.0.1)', text)
        self.assertIn('| macOS | X64 | [156.0.1.4]', text)
        self.assertTrue(text.startswith(head))
        self.assertTrue(text.endswith(tail))

    def test_real_readme_is_refreshed_by_the_workflow_script(self):
        # Mirrors the publish job: run the script on a copy of the real README.md.
        # Asserts only structure, so version bumps or prose edits never break it.
        original = (Path(__file__).resolve().parents[1] / 'README.md').read_text(encoding='utf-8')
        repository = re.search(r'https://github\.com/([^/]+/[^/]+)/releases/tag/', original).group(1)
        before = readme.parse_table(original, repository)
        self.assertEqual(len(before), 7, 'every platform/architecture row must be readable')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.discovery(root, 'linux', 'x64', {'stable': '999.0.0.1'})
            path = root / 'README.md'
            path.write_text(original, encoding='utf-8')
            sys.argv = ['update_readme.py', '--input', str(root), '--readme', str(path),
                        '--repository', repository]
            readme.main()
            updated = path.read_text(encoding='utf-8')
        after = readme.parse_table(updated, repository)
        self.assertEqual(after[('linux', 'x64')], {'stable': '999.0.0.1'})
        self.assertEqual({k: v for k, v in after.items() if k != ('linux', 'x64')},
                         {k: v for k, v in before.items() if k != ('linux', 'x64')})
        outside = lambda text: (text.split(readme.START)[0], text.split(readme.END)[1])
        self.assertEqual(outside(updated), outside(original))

    def test_missing_markers_are_rejected(self):
        with self.assertRaises(ValueError):
            readme.update_readme('no markers', 'table')


if __name__ == '__main__':
    unittest.main()
