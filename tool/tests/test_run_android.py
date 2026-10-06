"""Reconnect regression for the existing Android development launcher."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'run_android.sh'


class ForwardingRecoveryTest(unittest.TestCase):
    def test_watch_restores_forwarding_after_device_address_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / 'device'
            state.write_text('phone:1000')
            (root / 'curl').write_text('#!/bin/sh\nexit 0\n')
            (root / 'adb').write_text('''#!/bin/sh
if [ "$1" = devices ]; then
  printf 'List of devices attached\\n%s device\\n' "$(cat "$TEST_DEVICE_STATE")"
elif [ "$3" = shell ]; then
  printf 'physical-phone\\n'
elif [ "$3" = reverse ] && [ "$4" = --list ]; then
  exit 0
elif [ "$3" = reverse ]; then
  printf '%s\\n' "$2" >> "$TEST_FORWARD_LOG"
fi
''')
            for name in ('adb', 'curl'):
                (root / name).chmod(0o755)
            log = root / 'forwarded'
            env = {**os.environ, 'PATH': f'{root}:{os.environ["PATH"]}',
                   'TEST_DEVICE_STATE': str(state), 'TEST_FORWARD_LOG': str(log)}
            process = subprocess.Popen(['bash', str(SCRIPT), '--watch'], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.wait_for(log, 'phone:1000', process)
                state.write_text('phone:2000')
                self.wait_for(log, 'phone:2000', process)
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def wait_for(self, log, value, process):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if log.exists() and value in log.read_text():
                return
            if process.poll() is not None:
                self.fail(process.communicate()[1].decode())
            time.sleep(0.05)
        self.fail(f'forwarding was not restored for {value}')


if __name__ == '__main__':
    unittest.main()
