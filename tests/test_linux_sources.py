import gzip
import hashlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import linux_sources as sources


class RpmTests(unittest.TestCase):
    def tearDown(self):
        sources.rpm_sources.cache_clear()

    def fixture(self, arch='x86_64', href='google-chrome-stable-156.0.0.1-1.x86_64.rpm', corrupt=False):
        primary = gzip.compress(f'''<metadata xmlns="http://linux.duke.edu/metadata/common">
          <package><name>google-chrome-beta</name><arch>{arch}</arch><version ver="157.0.0.1"/><location href="ignore.rpm"/></package>
          <package><name>google-chrome-stable</name><arch>{arch}</arch><version ver="156.0.0.1"/><location href="{href}"/></package>
        </metadata>'''.encode())
        digest = '0' * 64 if corrupt else hashlib.sha256(primary).hexdigest()
        repomd = f'''<repomd xmlns="http://linux.duke.edu/metadata/repo"><data type="primary">
          <location href="repodata/primary.xml.gz"/><checksum type="sha256">{digest}</checksum><size>{len(primary)}</size>
        </data></repomd>'''.encode()
        return [io.BytesIO(repomd), io.BytesIO(primary)]

    def test_versioned_rpm_is_selected_by_architecture_and_stable_package(self):
        with patch.object(sources.OPENER, 'open', side_effect=self.fixture()) as opened:
            result = sources.rpm_sources('x64')
        self.assertEqual(result, {'156.0.0.1': 'https://dl.google.com/linux/chrome/rpm/stable/x86_64/google-chrome-stable-156.0.0.1-1.x86_64.rpm'})
        self.assertTrue(opened.call_args_list[0].args[0].endswith('/x86_64/repodata/repomd.xml'))
        sources.rpm_sources.cache_clear()
        with patch.object(sources.OPENER, 'open', side_effect=self.fixture(arch='aarch64', href='google-chrome-stable-156.0.0.1-1.aarch64.rpm')):
            self.assertIn('.aarch64.rpm', sources.rpm_sources('arm64')['156.0.0.1'])

    def test_wrong_architecture_is_ignored(self):
        with patch.object(sources.OPENER, 'open', side_effect=self.fixture(arch='aarch64')):
            self.assertEqual(sources.rpm_sources('x64'), {})

    def test_corrupt_metadata_and_floating_or_escaping_links_are_rejected(self):
        for kwargs in [dict(corrupt=True), dict(href='google-chrome-stable_current_x86_64.rpm'),
                       dict(href='../google-chrome-stable-156.0.0.1-1.x86_64.rpm')]:
            with self.subTest(kwargs=kwargs):
                sources.rpm_sources.cache_clear()
                with patch.object(sources.OPENER, 'open', side_effect=self.fixture(**kwargs)), self.assertRaises(ValueError):
                    sources.rpm_sources('x64')
        for href in ['https://evil.example/primary.xml.gz', '/repodata/primary.xml.gz', '../primary.xml.gz']:
            with self.subTest(href=href), self.assertRaises(ValueError):
                sources.repository_url('https://dl.google.com/', href)


if __name__ == '__main__':
    unittest.main()
