import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import archive_logging as logs
import chrome_archive as archive


class LogTests(unittest.TestCase):
    def test_failed_stage_retains_context_timing_and_redacts_secrets(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), logs.log_context(platform='windows', architecture='x86', channel='stable', GH_TOKEN='do-not-print-this'):
            with self.assertRaises(ValueError), logs.stage('example'):
                raise ValueError('specific failure')
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(records[-1]['event'], 'stage_end')
        self.assertEqual(records[-1]['status'], 'error')
        self.assertEqual(records[-1]['error_type'], 'ValueError')
        self.assertEqual(records[-1]['architecture'], 'x86')
        self.assertGreaterEqual(records[-1]['wall_seconds'], 0)
        self.assertIn('self_cpu_seconds', records[-1])
        self.assertIn('finished_children_cpu_seconds', records[-1])
        self.assertNotIn('do-not-print-this', output.getvalue())

    def test_long_stage_emits_heartbeat_with_target_context(self):
        heartbeat = threading.Event()
        original = logs.log_event
        output = io.StringIO()
        def record(event, **fields):
            original(event, **fields)
            if event == 'stage_heartbeat':
                heartbeat.set()
        with contextlib.redirect_stdout(output), patch.object(logs, 'log_event', side_effect=record), logs.log_context(platform='linux', architecture='arm64'):
            with logs.stage('compress_browser_tar_xz', heartbeat_seconds=0.01):
                self.assertTrue(heartbeat.wait(timeout=2))
        records = [json.loads(line) for line in output.getvalue().splitlines()]
        status = next(item for item in records if item['event'] == 'stage_heartbeat')
        self.assertEqual(status['architecture'], 'arm64')
        self.assertIn('wall_seconds', status)

    def test_download_logs_progress_and_verification_failure_at_correct_stage(self):
        payload = b'fixture bytes'
        for digest, success in [(hashlib.sha256(payload).hexdigest(), True), ('0' * 64, False)]:
            with self.subTest(success=success), tempfile.TemporaryDirectory() as directory:
                offer = archive.Offer('windows', 'x86', 'stable', '156.0.0.1', 'fixture.crx3', len(payload), digest,
                                      ('https://dl.google.com/fixture.crx3',), 'installer.exe')
                output = io.StringIO()
                with contextlib.redirect_stdout(output), patch.object(archive.OPENER, 'open', return_value=io.BytesIO(payload)), patch.object(archive, 'DOWNLOAD_PROGRESS_SECONDS', 0):
                    if success:
                        self.assertEqual(archive.download(offer, Path(directory)).read_bytes(), payload)
                    else:
                        with self.assertRaises(RuntimeError):
                            archive.download(offer, Path(directory))
                records = [json.loads(line) for line in output.getvalue().splitlines()]
                progress = next(item for item in records if item['event'] == 'download_progress')
                self.assertEqual(progress['downloaded_bytes'], len(payload))
                ended = next(item for item in records if item['event'] == 'stage_end')
                self.assertEqual(ended['stage'], 'download')
                self.assertEqual(ended['status'], 'success' if success else 'error')


if __name__ == '__main__':
    unittest.main()
