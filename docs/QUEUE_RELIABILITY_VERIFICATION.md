# Phase 1: queue reliability verification

Verified on 2026-10-06. This phase does not change content intelligence, card match
labels, Brief finalization, or bulk sharing.

## Root cause and observed recovery

- The phone's SQLite queue contained four pending links; the backend had no
  pending jobs for them. They had never reached the API.
- No ADB reverse rule existed. A connection from the phone to `127.0.0.1:8000`
  was refused. The Android development launcher previously set forwarding once.
- Restoring forwarding allowed normal phone sync to upload all four links.
  The phone queue then contained nine done records and no pending records.
- Backend processing ran normally afterward. No queue or item status was edited
  manually to produce this result.

## Changes

- The existing Android launcher now maintains forwarding, identifies the
  physical phone, and handles a different wireless ADB address after reconnect.
  `tool/run_android.sh --watch` supports an already installed app.
- App resume, refresh and Retry upload can retry immediately while automatic
  retries retain backoff and the single-uploader guard.
- Connection failures are visible beside the queued-link count.
- Successful sync refreshes the library automatically.
- SQLite version 3 stores the returned server ID with a completed queue row.
  An opened queued memory can resolve its server record after sync and restart.

## Commands and results

From `mobile`:

```bash
flutter test --reporter expanded --concurrency=1
flutter analyze
FINDBACK_LIVE_QUEUE_TEST=1 flutter test test/live_queue_restart_test.dart --reporter expanded --concurrency=1
GRADLE_OPTS='-Dorg.gradle.jvmargs=-Xmx1024m -Dorg.gradle.workers.max=2 -Dorg.gradle.daemon=false' flutter build apk --debug
```

From the repository root:

```bash
bash -n tool/run_android.sh
python -m unittest discover -s tool/tests -v
adb install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
```

Observed results:

- Full Flutter suite: **97 passed, 1 skipped**. The live Compose test is skipped
  unless explicitly enabled.
- Opt-in live Compose test: **1 passed**. Actual CaptureService queued a capture
  during `docker compose down`; actual SyncService uploaded it after
  `docker compose up -d`. The saved link reused the existing READY memory, and
  the API item IDs were unchanged. The probe used a tracking-parameter variant
  of the previously saved Facebook reel.
- Launcher reconnect regression: **1 passed**. It restored forwarding after the
  mocked phone's wireless address changed.
- Analysis: **No issues found**. Shell syntax check passed. APK built and its
  update installed successfully.
- On the physical phone, removing forwarding deliberately caused the running
  watcher to restore it automatically.
- The installed phone database upgraded from version 2 to version 3. All nine
  existing queue records survived and remained done.
- Final backend health: API, database and Redis OK; all five Compose services
  running; eight jobs READY, none pending or processing.

The opt-in live test stops and restarts the local Compose stack and repeats a
save of the existing Facebook reel. It requires local development authentication
and that saved reel. Do not enable it in routine unit-test or hosted environments.

## Backup and limitations

Phone database backup before update:
`/home/moaz/.local/state/findback/backups/phone-before-queue-v3-20261006/`

This remains a local development setup: a localhost-configured phone needs its
paired ADB connection and the forwarding watcher. A standalone Wi-Fi deployment
needs an API_BASE_URL the phone can reach without ADB. The watcher is currently
running in the development session; restart it after restarting the host session.
No application can upload while its configured server is unreachable.

The attempted full phone UI restart test was aborted when the foreground app
changed to YouTube. A generic Save tap landed in YouTube; its playlist effect
was not confirmed. Phone tap automation was stopped, Compose was restored, and
restart recovery was verified through the actual mobile services instead.

The Android build emitted an SDK metadata-version warning but succeeded.
Gemini quota failures and fallback Brief retry behavior were observed and remain
for Phase 2, Brief finalization.
