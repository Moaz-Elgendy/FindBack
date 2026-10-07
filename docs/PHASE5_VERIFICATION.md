# Phase 5 verification

## Changes

The library card display model preserves `ItemDetail.isGeneratingBrief`, including cached/offline cards. A small fading light follows the existing rounded card outline while generation is active. No loading icon was added to cards. Flutter's animation controller drives painter repaints, not per-frame state updates to the home screen. Reduced-motion settings replace the moving light with a static outline. Controllers stop for finalized Briefs and dispose with their cards.

The refresh button and queued badge were replaced by `Processing N` in the app bar. The count includes server generation and offline uploads, without counting a local card and its queued upload twice. A real LLM Brief with text clears processing even if a stale status/retry flag remains. These are display changes using the existing item predicate; no backend state or retry rules changed.

While processing is present, the home screen refreshes saved metadata every five seconds and preserves displayed cards during background refresh. Polling stops when there is no remaining work, when the screen is disposed, or when the app leaves the foreground. It resumes through the existing refresh path. Metadata fetch failures retain the previous state and schedule the next refresh.

Library and search lists have a small arrow animation and `Pull down to refresh` cue at their top. The cue scrolls away with the list. Short and empty lists remain scrollable for refresh. The cue respects reduced motion, wraps at larger text sizes, and provides an accessibility refresh action. The existing native pull-to-refresh indicator remains.

No backend, API contract, database, migration, queue, or provider changes were made.

## Regression evidence

Before implementation, the three initial new tests failed for the missing border, reduced-motion border, and existing refresh button. The empty-library refresh test also failed before that behavior was added.

New tests cover active/fallback borders; final Briefs overriding stale retry state; reduced motion; automatic polling and shutdown; offline upload count deduplication; top refresh cue and short-list physics; and empty-library refresh. Existing large-text, pagination, sharing, search/filter, queue, and detail tests remain in the full suite.

From `mobile`:

```sh
flutter test test/result_card_test.dart test/library_ui_test.dart --timeout 30s --reporter expanded
flutter test --timeout 30s --reporter expanded
flutter analyze
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

Focused tests: **19 passed**. Full suite: **125 passed, 1 skipped**. Analyzer: **No issues found**. Debug APK: **built successfully**.

From repository root:

```sh
python .agents/skills/mobile-design/scripts/mobile_audit.py mobile
adb -s 192.168.1.8:38855 install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
adb -s 192.168.1.8:38855 reverse tcp:8000 tcp:8000
git -c core.whitespace=cr-at-eol diff --check
```

Generic skill audit: exit 1, **24 issue flags, 128 warnings, 48 passed checks**. [Raw output](ui-verification/phase5/mobile-audit.txt). Its size checks flag icon/text dimensions as touch targets and its framework checks recommend React/Jest tools for Dart files; this is not a passing accessibility audit. Existing widget tests verify actual filter targets and large-text layouts. Remaining audit candidates require verification in the later focused audit phase; unrelated files were not changed.

APK installation returned **Success**, preserving app data. ADB reverse succeeded. Diff check exited 0. Backend tests: **NOT RUN**, because this phase changed no backend code. Physical animation walkthrough and live backend processing on the phone: **NOT RUN**; the processing-to-final transition was verified in widget tests, not by modifying saved records.

## Limits and next phase

Automatic refresh discovers completion on the next poll, normally within five seconds after a successful fetch. Offline/unreachable-server state cannot establish new completion until connectivity returns. The existing paginated metadata scan supplies the processing count; a dedicated server count remains unnecessary for the current small library and was not added.

Phase 6 is Android sharing verification and improvement. Free-VM/Supabase deployment remains the final phase.
