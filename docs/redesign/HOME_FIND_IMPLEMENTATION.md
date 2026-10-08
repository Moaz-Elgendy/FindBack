# Home + Find implementation notes

Implemented the main brief's phase (c), including its prerequisites from the addendum. Collections page, grouping, service, and endpoints were left unchanged; the shared shell adds the specified centered Save entry point and keeps both existing destinations.

- Library's search launcher opens a full-screen Find route. The existing controller still debounces, rejects stale results, calls semantic/fuzzy backend search online, and labels the local fallback offline. Find immediately focuses its multiline input and retains the input during loading.
- Chips wrap. Four potential type/date choices plus the two most frequent classified topics are offered only when they match saved metadata on their own (six maximum). Classification, not keywords in titles, determines topics. Type/topic choices select one value per dimension; other dimensions combine. Existing advanced filters remain under More filters so less common topics/entities/intents/sources are still reachable.
- Empty-query Find browses newest saves with the selected filters. The last-two-weeks cutoff is an inclusive absolute UTC instant, sent separately as saved_after, and applied before server/SQLite limits.
- Only explicit backend matched_terms produce Matched text. Semantic-only evidence gets a neutral Similar to what you described. No fake matched terms are inferred by the UI.
- Reading N combines queued and processing saves without double-counting. Queued cards describe phone-only storage; processing and failed cards appear first. Empty Library provides the Share/Read/Find panel. Duplicates show Already saved with View; normal share/capture feedback says Saved. Reading it now.
- Card menus and custom screen-reader actions use Edit, Summarize again, Delete. Edit saves real backend overrides or completed guest data locally. A queued save must finish uploading before editing; the sheet retains changes and explains the failure. Confirmed explicit reprocessing alone can replace edits; failed processing keeps the previous brief, vectors, and chunks and displays a safe toast.
- Undo deletes immediately, snapshots SQLite item/queue data, waits for in-flight ID mapping, and restores the server before the local mirror. Collections membership is not mutated by this path. The toast lasts five seconds, or ten with accessible navigation, and announces Undo. The server retains/restores soft-deleted saves for 30 days. Offline deletion of an existing server memory retains the previous device-only behavior and says so; it is not silently queued as a server deletion.
- Guest reprocessing re-ingests the actual URL once. Its previous brief is retained durably in the existing SQLite payload while temporary staging runs; failed replacement restores it and its edited marker. Automatic guest refresh cannot overwrite edits.
- New-install appearance is Auto. Stored explicit choices survive. Inputs/unselected chips/outlined buttons use the stronger control border; Recipe is rose. Both UI and serif summary styles fall back to bundled OFL Noto Sans Arabic. New layouts use directional padding/alignment.

Deliberate differences from the prototype: no fake data or phone frames; no keyword-based semantic filtering; real backend evidence only; native scrolling for a multiline input, wrapping chips, and large text; More filters retains existing capabilities beyond the prototype. Flutter card summaries retain their three-line expansion rather than copying the HTML's fake bullet structure.

## Verification

The backend full suite passed 888 tests with one skip. Flutter full suite passed **240 tests with one skip**; exact output is in /tmp/findback-home-find-full-final.log; Home/Find layout+RTL+empty checks passed 26 tests in /tmp/findback-home-find-layout-final.log. Flutter analysis reported No issues found. Debug APK built at mobile/build/app/outputs/flutter-apk/app-debug.apk. Diff checks and Python compilation passed. Test-only Postgres database was dropped; migration 0017 was exercised in throwaway fixture databases, not deployed to application data.

Screenshots: /tmp/findback-home-{light,dark}-{360,390}.png and /tmp/findback-find-{light,dark}-{360,390}.png. The widgets were also checked at 320dp and text scales 1.0, 1.3, 2.0, including RTL/mixed Arabic and English, empty state, header/chips/navigation. These are visual review artifacts, not assertions of pixel identity with a web prototype. Hardware TalkBack/VoiceOver and live-provider processing were not exercised.

## Remaining redesign phases

Memory detail/reminders (d), Account (e), and notifications/Worth another look (f) are not part of this Home+Find implementation. Their backend gaps are explicit in ADDENDUM_AUDIT.md: absolute reminder/time-zone storage and delivery; opens tracking for the seven-day-never-opened definition; weekly snapshots and private notification transport; export/account-deletion contracts. No disabled or fake versions were added. Collections remains out of scope under the addendum.

Final queued-save regression: an empty server library now renders the first locally queued save before choosing an empty state. The regression was observed failing, then passed after the feed check moved.

Final commands (mobile directory):
```sh
flutter test test/library_ui_test.dart --plain-name 'first queued save'
flutter test --reporter expanded
flutter analyze
flutter build apk --debug
```
Results: focused regression 1 passed; full suite 240 passed, 1 skipped; analysis No issues found; debug APK built successfully. Repository checks: `git diff --check` and `python -m compileall -q backend/app backend/alembic` passed.
