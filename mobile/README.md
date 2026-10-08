# FindBack — Flutter client

The mobile app: save a link from anywhere, and find it again later by vague
memory. This replaces the Expo/React Native client; the offline contract
(SQLite mirror + write queue + `POST /api/v1/sync/batch`) is carried over
unchanged, so an upgraded install keeps its cached memories.

**Requirements:** Flutter stable with Dart ≥ 3.13 (3.47+) and, for device builds,
Android Studio with SDK 37 or Xcode. `android/` and `ios/` are *not* checked in — they are
generated per developer machine.

## Bootstrap & run

```bash
tool/setup_mobile.sh        # from the repo root: generates android/ + ios/, then pub get + analyze + test

cd mobile
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

`10.0.2.2` is how an Android emulator reaches the host's `localhost`; use
`http://localhost:8000` for the iOS simulator. Against a backend that runs with
`DEV_AUTH_ENABLED=false`, add `--dart-define=API_TOKEN=<token>` — it seeds the
token store so the dev path keeps working without a secret in source.

## Physical Android device (USB or wireless debugging)

Connect the phone to WSL's ADB first. From the repository root, run:

```bash
docker compose up --build
# In another WSL terminal:
tool/run_android.sh
# With more than one device, pass the ID shown by adb devices:
# tool/run_android.sh 192.168.1.8:40765
```

The The launcher checks the local API and connected device, establishes forwarding,
and keeps restoring it while Flutter runs. It identifies the physical phone so
wireless reconnects with a different ADB port are handled.

For an already installed APK, keep forwarding alive in a separate terminal:

```bash
tool/run_android.sh --watch
```

This watches the paired phone and restores `tcp:8000` forwarding automatically.
The loop can stay running across Compose restarts. Stop it with Ctrl+C. This is
local development routing: the phone still needs an ADB connection to the laptop.
For use without ADB, build with `--dart-define=API_BASE_URL=<reachable backend URL>`;
`localhost` on the phone refers to the phone, not the laptop.

Queued links remain on-device while the backend is unreachable. The app exposes
failed uploads and offers **Retry upload**, retries on resume, and refreshes the
library after a successful upload. SQLite version 3 preserves queued links and
stores their returned server IDs so an already-open memory keeps working after sync.

Nothing is read from `.env` at runtime: a compiled binary has no `.env`, so all
configuration arrives through `--dart-define` (`lib/config.dart`).

## Layout

| Path | What lives there |
| --- | --- |
| `lib/data/api_client.dart` | Dio wrapper; every failure becomes a typed `ApiException` (`connectivity`, `timeout`, `unauthorized`, `server`, `rejected`, `malformed`) |
| `lib/data/local_db.dart` | SQLite mirror (`items`) + offline write queue (`sync_queue`), opened with WAL |
| `lib/data/token_store.dart` | Bearer token in Keychain / encrypted prefs |
| `lib/services/capture_service.dart` | The Save path: API first, keep it on device when the network is the problem |
| `lib/services/items_service.dart` | Detail/list/delete reads with cache fallbacks |
| `lib/services/sync_service.dart` | Drains the queue with exponential backoff |
| `lib/app_services.dart` | Composition root: builds the graph once in `main` |
| `lib/features/home/` | Search controller, home screen, detail page, capture sheet, result card |

## Offline-first behaviour

- **Save while offline** — the capture is written to `sync_queue` *and* an
  optimistic `items` row with id `local-<client_id>`, so it is searchable and
  visible in Recent immediately. A badge in the app bar shows queue depth.
- **Send later** — `SyncService` flushes on launch, on every connectivity
  transition and on a 30 s heartbeat. Rejections double the wait from 1 s up to a
  5 minute ceiling, and the timer honours that window (the RN client computed a
  backoff the 30 s timer ignored, so a downed API was hammered forever).
- **Server ids replace local ids** — a mapped save renames `local-<client_id>` to
  the server UUID instead of leaving a permanent ghost row.
- **Give up rather than loop** — a row the server refuses three times is parked
  as `failed` and stops riding the heartbeat; its local copy stays on the device
  so the user can still see and delete it.
- **Reads degrade** — search, Recent and detail fall back to the mirror and the
  UI says "Offline" instead of spinning. A `local-*` id is never sent to the API.
- **Deletes are local first** — the item always disappears from this device; the
  server copy is deleted too when reachable. Deleting an unsaved capture also
  drops its queue row, so a cancelled save cannot resurrect itself.
- Only connectivity-shaped failures fall back silently: a `4xx` rejection is a
  real answer and is shown to the user.

## Tests

```bash
cd mobile && flutter test
```

| File | Pins down |
| --- | --- |
| `test/local_db_test.dart` | Real SQLite via `sqflite_common_ffi`: queue → optimistic row, id adoption, parked rows, un-resurrect |
| `test/sync_service_test.dart` | Offline silence, single-flight flush, backoff growth/cap/reset, failure reporting |
| `test/capture_service_test.dart` | Remote vs queued saves; rejections are surfaced, not swallowed |
| `test/items_service_test.dart` | Cached/offline fallbacks, delete outcomes, no API call for `local-*` |
| `test/search_controller_test.dart` | Debounce, offline results, stale-response discard, fallback labelling |
| `test/models_test.dart`, `test/share_text_test.dart` | Wire/row parsing tolerance and share-sheet text extraction |

## Platform and account status

- **Native share intake.** Android single/multiple text sharing and recovery links are implemented and verified. iOS Share Extension behavior has not been verified. See `docs/NATIVE_SHARE.md`.
- **Optional accounts.** Supabase email/password authentication opens from the top-right account icon. Guests open the library directly. Build with public `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` Dart defines (legacy `SUPABASE_ANON_KEY` is also accepted). Never pass a secret/service-role key. Allow `findback://auth/recovery` in Supabase redirect URLs for password reset. Guest Briefs stay on the device after temporary processing; sign-in imports them through the normal queue. Account caches and queues are isolated; logout starts a fresh guest scope. See `docs/PHASE7_VERIFICATION.md` for tested behavior and deployment limits.
- `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md` still describe the Expo
  client. The data model, queue contract and API surface they document are
  unchanged; only the client stack is.
