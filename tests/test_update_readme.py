"""Check the README release table generated from discovery records."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import update_readme as readme

README = Path(__file__).resolve().parents[1] / 'README.md'


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
            path.write_text(README.read_text(encoding='utf-8').replace(
                '| macOS | ARM64 | — | — |',
                '| macOS | ARM64 | [155.0.0.1](https://github.com/o/r/releases/tag/155.0.0.1) | — |'), encoding='utf-8')
            sys.argv = ['update_readme.py', '--input', str(root), '--readme', str(path), '--repository', 'o/r']
            readme.main()
            text = path.read_text(encoding='utf-8')
        self.assertIn('[155.0.0.1](https://github.com/o/r/releases/tag/155.0.0.1)', text)
        self.assertIn('| macOS | X64 | [156.0.1.4]', text)
        self.assertTrue(text.startswith('# Chrome installer archive'))
        self.assertIn('## 自动执行', text)

    def test_missing_markers_are_rejected(self):
        with self.assertRaises(ValueError):
            readme.update_readme('no markers', 'table')


if __name__ == '__main__':
    unittest.main()
