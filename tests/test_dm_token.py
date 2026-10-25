"""Synthetic policy caches only: no real token, registry or browser mutations."""
import base64
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest


HELPERS = Path(__file__).resolve().parents[1] / 'scripts/chrome-dm-token'
SCRIPT = HELPERS / 'fix-dm-token.sh'
MACHINE = '0123456789abcdef0123456789abcdef'
SERIAL = 'SyntheticMacSerial'
TOKEN = b'unit-test-only-token'


def filename(value):
    return base64.urlsafe_b64encode(hashlib.sha1(value.encode()).digest()).decode().rstrip('=')


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number, value):
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    if isinstance(value, str):
        value = value.encode()
    return varint((number << 3) | 2) + varint(len(value)) + value


def cache(device, *, token=TOKEN, stamp=None, state=0, kind='google/chrome/machine-level-user', extra=b''):
    policy = (field(1, kind) + field(2, int(time.time() * 1000) if stamp is None else stamp)
              + field(3, token) + field(8, device))
    if state is not None:
        policy += field(9, state)
    return field(3, policy + extra)


class BashRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.user = self.root / 'custom user data'
        self.policy = self.user / 'Policy/Machine Level User Cloud Policy'
        self.policy.parent.mkdir(parents=True)

    def run_bash(self, command, *args, platform='Linux'):
        env = dict(os.environ, HOME=str(self.root), LC_ALL='C')
        # Simulate host identity only. All parsing, hashing and writes are real.
        preamble = '''set -euo pipefail
source "$1"
shift
uname() { printf '%s\\n' "$TEST_PLATFORM"; }
cat() { printf '%s\\n' '0123456789abcdef0123456789abcdef'; }
ioreg() { printf '%s\\n' '    "IOPlatformSerialNumber" = "SyntheticMacSerial"'; }
'''
        env['TEST_PLATFORM'] = platform
        return subprocess.run(['/bin/bash', '-c', preamble + command, 'test', str(SCRIPT), *map(str, args)],
                              env=env, text=True, capture_output=True)

    def target(self, platform):
        if platform == 'Linux':
            return self.user / 'Policy/Enrollment' / filename(MACHINE)
        return self.root / 'Library/Application Support/Google/Chrome Cloud Enrollment' / filename(SERIAL)

    def test_restore_and_repeat_on_both_platforms(self):
        for platform, device in [('Linux', filename(MACHINE)), ('Darwin', SERIAL)]:
            with self.subTest(platform=platform):
                self.policy.write_bytes(cache(device, state=None))
                result = self.run_bash('main --user-data-dir "$1"', self.user, platform=platform)
                self.assertEqual(result.returncode, 0, result.stderr)
                target = self.target(platform)
                self.assertEqual(target.read_bytes(), TOKEN)
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                before = target.stat().st_mtime_ns
                self.policy.unlink()  # Existing token skips even cache access.
                result = self.run_bash('main --user-data-dir "$1"', self.user, platform=platform)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('already exists', result.stdout)
                self.assertEqual(target.stat().st_mtime_ns, before)
                self.assertNotIn(TOKEN.decode(), result.stdout + result.stderr)

    def test_invalid_caches_never_create_token(self):
        device = filename(MACHINE)
        cases = {
            'wrong device': cache('other-machine'),
            'wrong type': cache(device, kind='google/chrome/user'),
            'inactive': cache(device, state=1),
            'old': cache(device, stamp=int((time.time() - 90000) * 1000)),
            'future': cache(device, stamp=int((time.time() + 600) * 1000)),
            'zero time': cache(device, stamp=0),
            'empty token': cache(device, token=b''),
            'long token': cache(device, token=b'x' * 4097),
            'control token': cache(device, token=b'bad\x00token'),
            'non ascii': cache(device, token=b'bad\xfftoken'),
            'invalidated': cache(device, token=b'INVALID_DM_TOKEN'),
            'duplicate token': cache(device, extra=field(3, b'another-token')),
            'wrong wire': cache(device, extra=field(8, 1)),
            'truncated varint': b'\x1a\x80',
            'truncated field': b'\x1a\x04a',
            'zero field': b'\x00',
            'overflow': b'\x80' * 10,
            'duplicate policy': cache(device) * 2,
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.policy.write_bytes(data)
                result = self.run_bash('main --user-data-dir "$1"', self.user)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.target('Linux').exists())
                self.assertNotIn(TOKEN.decode(), result.stdout + result.stderr)

    def test_unknown_fields_are_skipped_and_empty_payload_is_rejected(self):
        self.policy.write_bytes(cache(filename(MACHINE), extra=b'\x51' + b'\0'*8 + b'\x5d' + b'\0'*4))
        result = self.run_bash('main --user-data-dir "$1"', self.user)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.policy.write_bytes(b'\x1a\x00')
        result = self.run_bash('load_cached_token "$1" "$2"', self.policy, filename(MACHINE))
        self.assertNotEqual(result.returncode, 0)

    def test_existing_empty_value_and_dangling_symlink_are_preserved(self):
        target = self.target('Linux')
        target.parent.mkdir(parents=True)
        for symlink in [False, True]:
            with self.subTest(symlink=symlink):
                if symlink:
                    target.symlink_to(self.root / 'missing')
                else:
                    target.touch()
                result = self.run_bash('main --user-data-dir "$1"', self.user)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.is_symlink(), symlink)
                target.unlink()

    def test_writer_rechecks_destination(self):
        target = self.root / 'test-token'
        target.write_bytes(b'already-present')
        result = self.run_bash('TOKEN=synthetic-new; persist_token "$1"', target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), b'already-present')

    def test_linux_environment_default_paths(self):
        # The environment default uses the same directory as --user-data-dir.
        self.policy.write_bytes(cache(filename(MACHINE)))
        result = self.run_bash('export CHROME_USER_DATA_DIR="$1"; main', self.user)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.target('Linux').read_bytes(), TOKEN)

    def test_real_entrypoint_with_temporary_profile(self):
        if sys.platform == 'linux':
            machine = Path('/etc/machine-id').read_text().rstrip(' \t\r\n\v\f')
            device = filename(machine)
            target = self.user / 'Policy/Enrollment' / device
        elif sys.platform == 'darwin':
            output = subprocess.check_output(['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], text=True)
            device = re.search(r'"IOPlatformSerialNumber" = "([^"]+)"', output).group(1)
            target = self.root / 'Library/Application Support/Google/Chrome Cloud Enrollment' / filename(device)
        else:
            self.skipTest('Native Bash entrypoint is for Linux/macOS.')
        self.policy.write_bytes(cache(device))
        result = subprocess.run(['/bin/bash', str(SCRIPT), '--user-data-dir', str(self.user)],
                                env=dict(os.environ, HOME=str(self.root)), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(target.read_bytes(), TOKEN)


if __name__ == '__main__':
    unittest.main()
