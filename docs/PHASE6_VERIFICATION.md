# Phase 6 — Android sharing verification

Verified 2026-10-07 on the connected Samsung phone and local Compose backend.

## Scope and fix

Android already declared SEND and SEND_MULTIPLE for text/plain. Both use the existing method-channel, URL extraction, capture service, SQLite queue, and batch upload paths. No replacement sharing plugin or new endpoint was added.

A startup race was reproduced: MainActivity emitted a warm share before Dart subscribed to the broadcast stream, then cleared the intent. MainActivity now buffers launch/early shares until Dart calls getInitialShare. That call drains the buffered payload once; later shares use the existing onShare callback. Dart already subscribes before calling getInitialShare. The share-target label now uses the correct brand capitalization, FindBack.

The native regression compiles the actual MainActivity against small Android/Flutter boundary doubles with the Kotlin compiler already cached by Gradle. Before the fix it failed with `Share emitted before Dart listener was ready`. After the fix it checks startup buffering, consume-once delivery, warm delivery, twenty-link SEND_MULTIPLE payloads, ignored unrelated intents, and manifest label/registration. This is a JVM boundary test, not Android instrumentation; the installed APK and live intents separately verify native integration.

## Live phone and data evidence

The app was stopped before taking a consistent backup of its databases and journal files:

`/home/moaz/.local/state/findback/backups/phase6-shares-before/databases.tar`

The backup used exclusive creation, mode 0600, and was 291,328 bytes. Extracted SQLite integrity_check returned ok; it contained 11 cached items and 29 completed queue rows. Existing cached/user rows were not removed.

The new APK installation returned Success with `adb install -r`, preserving app data. Android's package manager resolves FindBack for both single and multiple text shares.

A cold SEND with `https://www.facebook.com/share/r/19PXq2Y3AR/` and a warm SEND with the already-saved `https://www.facebook.com/share/r/19yKhyE3sc/` reached the existing ingest endpoint: two HTTP 200 responses. The cold launch was confirmed with `am start -W` (LaunchState COLD); the warm share reached the existing activity.

A warm SEND_MULTIPLE contained twenty distinct raw links to the first reel, varying only `utm_source=findback-phase6-N`. Android's `--esal` option supplied the ArrayList extra. The existing queue and normal sync completed a POST to `/api/v1/sync/batch` with HTTP 200. Final phone state:

- 20 test queue rows done, representing 20 distinct raw URLs.
- One distinct server_id: `7c8e9b90-10b8-4bb8-a307-e44180a9452b`.
- Zero remaining optimistic test rows.
- SQLite integrity_check: ok.

The real backend retained exactly three saved items. IDs, statuses, and complete stored Brief JSON were compared before/after and remained identical; the snapshot SHA-256 was `5afb528ada55c17d03fcb53eb35a9a6dfd574de1c7feed320b96043c66b41dc2` both times. No forced reprocessing, manual state changes, schema migration, or new LLM evaluation was performed.

[Live result artifact](ui-verification/phase6/live-result.json).

## Exact automated commands and results

From repository root:

```sh
python mobile/test/android_share_test.py
```

Native checks passed. This command requires the cached Kotlin 2.2.21 compiler/dependencies populated by Gradle; it does not download a new dependency.

From mobile:

```sh
flutter test test/share_intent_test.dart test/multiple_links_test.dart test/capture_service_test.dart test/library_ui_test.dart --timeout 30s --reporter expanded
flutter test --timeout 30s --reporter expanded
flutter analyze
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

Focused tests: **34 passed**. Full Flutter suite: **125 passed, 1 skipped**. Analyzer: **No issues found**. Debug APK: **built successfully**.

From repository root:

```sh
adb -s 192.168.1.8:38855 install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
adb -s 192.168.1.8:38855 reverse tcp:8000 tcp:8000
adb -s 192.168.1.8:38855 shell cmd package query-activities --brief -a android.intent.action.SEND -t text/plain
adb -s 192.168.1.8:38855 shell cmd package query-activities --brief -a android.intent.action.SEND_MULTIPLE -t text/plain
adb -s 192.168.1.8:38855 shell am start -W -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT https://www.facebook.com/share/r/19PXq2Y3AR/ -n com.findback.findback/.MainActivity
adb -s 192.168.1.8:38855 shell am start -W -a android.intent.action.SEND -t text/plain --es android.intent.extra.TEXT https://www.facebook.com/share/r/19yKhyE3sc/ -n com.findback.findback/.MainActivity
git -c core.whitespace=cr-at-eol diff --check
```

The multiple-intent command used the same am start flags with SEND_MULTIPLE and `--esal android.intent.extra.TEXT` followed by the comma-joined twenty URLs described above. Installation, reverse, native registrations, and intent launches succeeded; diff check exited 0.

Backend tests: **NOT RUN**, because no backend code changed. Native Facebook share-sheet navigation/pinning and iOS sharing: **NOT RUN**. ADB intents establish Android delivery and persistence; they do not establish where Facebook visually places the target.

## Limits and next phase

Android/the sending app control share-target ordering. FindBack cannot force first placement or add itself to Facebook's private shortcut strip. [Native sharing instructions](NATIVE_SHARE.md) describe optional user pinning/favorites where the system supports them.

Pre-listener buffering is in-memory; an intent not yet captured to SQLite is not durable against process termination. Once queued, the existing persistent sync path handles restart/retry. No background Android service was added, and iOS share extensions remain outside this Android phase.

Phase 7 is Supabase profiles/sign-in and account isolation. Free-VM/Supabase database deployment remains the final phase.
