#!/usr/bin/env bash
# Run the Android app against the local Compose API, including wireless ADB.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mapfile -t devices < <(adb devices | awk 'NR > 1 && $2 == "device" {print $1}')
device_id="${1:-}"
if [[ -z "$device_id" ]]; then
  if [[ ${#devices[@]} -ne 1 ]]; then
    echo 'Connect one Android device, or pass its ADB device ID.' >&2
    exit 1
  fi
  device_id="${devices[0]}"
else
  found=false
  for candidate in "${devices[@]}"; do
    [[ "$candidate" == "$device_id" ]] && found=true
  done
  if [[ "$found" != true ]]; then
    echo "Android device $device_id is not connected." >&2
    exit 1
  fi
fi

if ! curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health >/dev/null; then
  echo 'Local API is unavailable. Start docker compose up --build first.' >&2
  exit 1
fi
adb -s "$device_id" reverse tcp:8000 tcp:8000
cd "$repo_root/mobile"
exec flutter run -d "$device_id" --dart-define=API_BASE_URL=http://127.0.0.1:8000
