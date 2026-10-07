# Phase 5: Multiple-link sharing

Verified on 2026-10-07.

## Implementation

Sharing and pasting use the same URL extraction and capture path. Unique URLs are saved in their original order. Multiple URLs are written to the existing SQLite queue before network access; the existing sync service uploads them through the existing batch endpoint. The paste sheet retains failed URLs for retry. Single-link duplicates retain the centered “Already exists” dialog.

Android SEND and SEND_MULTIPLE text handling is implemented in MainActivity and registered in the manifest. These two files are included despite the generated Android directory otherwise being ignored.

The connected phone had SQLite version 3 without the `sync_queue.server_id` column. SQLite version 4 checks for and adds the missing column during upgrade. Existing rows are retained. A backup was taken at `/home/moaz/.local/state/findback/backups/phase5-before-v4/databases.tar` and its SQLite integrity check passed. After installing and launching the APK, the phone reported version 4, the column existed, and integrity_check returned `ok`.

## Automated verification

Commands run from `mobile/`:

- `flutter test --reporter expanded --concurrency=1`: **110 passed, 1 skipped**. Includes twenty distinct links, duplicate URLs, punctuation, initial and live shares, partial storage failure, failed-link retry in the paste sheet, and a real SQLite version-3 database missing the mapping column. The latter preserves twenty queued links through upgrade/restart and uploads all twenty through SyncService.
- `flutter analyze`: **No issues found**.
- `flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000`: **Built app-debug.apk**.
- `git -c core.whitespace=cr-at-eol diff --check`: no errors.

The existing platform-only skip remains. Backend tests were not rerun: no backend source was changed in this phase.

## Live verification

- api, outbox, postgres, redis, and worker were running; API health returned HTTP 200.
- Wi-Fi ADB connected to the Samsung phone. Port 8000 reverse forwarding was restored.
- `adb install -r mobile/build/app/outputs/flutter-apk/app-debug.apk`: **Success**, preserving app data.
- Android registered FindBack for SEND and SEND_MULTIPLE with text/plain.
- The current Compose PostgreSQL database was empty, including application tables. `docker compose exec -T api alembic upgrade head` applied existing migrations; the resulting revision was **0013_job_attempt_token**. No backup restoration was performed and no new backend migration was created.
- A cold-launch SEND intent with the user's Facebook reel reached `POST /api/v1/ingest` (HTTP 200), and the reel was persisted in the active database.
- A warm SEND_MULTIPLE intent contained twenty distinct raw URLs for that reel, varying only `utm_source=findback-phase5-N`. This tested batch capture without adding unrelated content or twenty separate LLM jobs.
- The phone stored all twenty queue entries. Normal batch sync returned HTTP 200. Final SQLite state was **20 done**, **one distinct server_id**, **zero remaining optimistic test rows**, and integrity_check **ok**. The backend contained one reel record, consistent with canonical URL deduplication.
- At the final database check, the newly saved reel was **processing**, with no stored Brief yet. Worker logs showed Gemini HTTP 503. This phase does not claim that a real LLM Brief completed.

## Limits

Batch upload follows the existing sync heartbeat and Android lifecycle restrictions. This phase does not provide background execution after Android stops the app. The earlier saved database state is absent from the currently running database; the newly saved reel is a new record. Media extraction/provider errors and restoration of the earlier database remain separate tasks.
