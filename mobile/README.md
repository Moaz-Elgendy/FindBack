# FindBack — Flutter client

The mobile app: save a link from anywhere, and find it again later by vague
memory. This replaces the Expo/React Native client; the offline contract
(SQLite mirror + write queue + `POST /api/v1/sync/batch`) is carried over
unchanged, so an upgraded install keeps its cached memories.

**Requirements:** Flutter stable with Dart ≥ 3.4 (3.22+) and, for device builds,
Android Studio or Xcode. `android/` and `ios/` are *not* checked in — they are
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

The launcher checks the local API and connected device, then runs
`adb reverse tcp:8000 tcp:8000` before Flutter. This makes the phone's
`127.0.0.1:8000` reach the API on the development machine. Forwarding must be
restored after reconnecting wireless debugging. Plain `flutter run` targets
localhost too, but does not establish this forwarding itself.

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

## Not done yet

- **Native share intake.** The Android `ACTION_SEND` intent-filter and the iOS
  Share Extension target are native work on top of the generated projects: the
  extension should write `{url, title, preview, timestamp, client_id}` into the
  shared container and exit without touching the network — exactly the shape
  `sync_queue` already accepts. The deleted RN config plugin
  (`plugins/withFindBackShare.js`) and `docs/OPERATIONS.md` are the reference for
  signing and App Group setup, and neither path has been verified on a device.
- **Auth UI.** `AppConfig.supabaseUrl` / `supabaseAnonKey` and
  `AppConfig.authConfigured` are in place, but no sign-in flow writes a token
  yet; the app runs on the dev token path.
- `docs/ARCHITECTURE.md` and `docs/OPERATIONS.md` still describe the Expo
  client. The data model, queue contract and API surface they document are
  unchanged; only the client stack is.
