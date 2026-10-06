#!/usr/bin/env bash
# Keep the development API reachable over USB or wireless ADB.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
watch_only=false
if [[ "${1:-}" == --watch ]]; then
  watch_only=true
  shift
fi
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
physical_serial="$(adb -s "$device_id" shell getprop ro.serialno | tr -d '\r')"
if [[ -z "$physical_serial" ]]; then
  echo 'Could not identify the connected phone.' >&2
  exit 1
fi

watch_backend() {
  while true; do
    mapfile -t connected < <(adb devices | awk 'NR > 1 && $2 == "device" {print $1}')
    for candidate in "${connected[@]}"; do
      serial="$(adb -s "$candidate" shell getprop ro.serialno 2>/dev/null | tr -d '\r' || true)"
      [[ "$serial" == "$physical_serial" ]] || continue
      if ! adb -s "$candidate" reverse --list 2>/dev/null | awk '$2 == "tcp:8000" && $3 == "tcp:8000" {found=1} END {exit !found}'; then
        if adb -s "$candidate" reverse tcp:8000 tcp:8000; then
          echo "Backend forwarding restored for $candidate"
        fi
      fi
    done
    sleep 2
  done
}

if [[ "$watch_only" == true ]]; then
  trap 'exit 0' INT TERM
  watch_backend
else
  if ! curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/health >/dev/null; then
    echo 'Local API is unavailable. Start docker compose up --build first.' >&2
    exit 1
  fi
  adb -s "$device_id" reverse tcp:8000 tcp:8000
  watch_backend &
  watch_pid=$!
  trap 'kill "$watch_pid" 2>/dev/null || true' EXIT
  cd "$repo_root/mobile"
  flutter run -d "$device_id" --dart-define=API_BASE_URL=http://127.0.0.1:8000
fi
