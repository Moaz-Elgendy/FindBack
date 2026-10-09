"""The local Android launcher must establish a route to the API before Flutter."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run_launcher(tmp_path, devices="phone device", api_ok=True, reverse_ok=True, args=()):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls"
    stubs = {
        "adb": 'if [ "$1" = devices ]; then printf "List of devices attached\\n%s\\n" "$MOCK_DEVICES"; elif [ "$3" = shell ]; then echo mock-phone-serial; elif [ "$4" = --list ]; then printf "phone tcp:8000 tcp:8000\\n"; else echo "adb $*" >> "$MOCK_LOG"; [ "$REVERSE_OK" = 1 ]; fi',
        "curl": 'echo "$*" >> "$MOCK_CURL_LOG"; [ "$API_OK" = 1 ]',
        "flutter": 'echo "flutter $* cwd=$PWD" >> "$MOCK_LOG"',
    }
    for name, body in stubs.items():
        file = binary / name
        file.write_text("#!/bin/sh\n" + body + "\n")
        file.chmod(0o755)
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ["PATH"],
               MOCK_DEVICES=devices, MOCK_LOG=str(log), MOCK_CURL_LOG=str(tmp_path / 'curlcalls'), API_OK=str(int(api_ok)),
               REVERSE_OK=str(int(reverse_ok)))
    result = subprocess.run(["bash", str(ROOT / "tool/run_android.sh"), *args],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    return result, log.read_text() if log.exists() else ""


def test_launcher_forwards_api_before_starting_flutter(tmp_path):
    result, calls = run_launcher(tmp_path)
    assert 'http://127.0.0.1:8000/ready' in (tmp_path / 'curlcalls').read_text()
    assert result.returncode == 0, result.stderr
    lines = calls.splitlines()
    assert lines[0] == "adb -s phone reverse tcp:8000 tcp:8000"
    assert lines[1] == ("flutter run -d phone --dart-define=API_BASE_URL=http://127.0.0.1:8000 "
                        f"cwd={ROOT / 'mobile'}")


@pytest.mark.parametrize("devices", ["", "phone device\nsecond device", "phone offline"])
def test_launcher_refuses_missing_or_ambiguous_device(tmp_path, devices):
    result, calls = run_launcher(tmp_path, devices=devices)
    assert result.returncode != 0
    assert "device" in result.stderr.lower()
    assert not calls


@pytest.mark.parametrize("failure", ["api", "reverse"])
def test_launcher_does_not_start_flutter_when_route_fails(tmp_path, failure):
    result, calls = run_launcher(tmp_path, api_ok=failure != "api", reverse_ok=failure != "reverse")
    assert result.returncode != 0
    assert "flutter" not in calls
    if failure == "api":
        assert "API" in result.stderr
        assert not calls


def test_launcher_uses_explicit_device_when_multiple_are_connected(tmp_path):
    result, calls = run_launcher(tmp_path, devices="phone device\nsecond device", args=("second",))
    assert result.returncode == 0
    assert calls.splitlines()[0] == "adb -s second reverse tcp:8000 tcp:8000"
