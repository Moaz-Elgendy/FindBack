# Phase 3 — topic filtering verification

Historical Phase 3 evidence. Its keyword grouping and Other bucket were superseded in Phase 4; see [Phase 4 verification](PHASE4_VERIFICATION.md).

## Scope and behavior

The selector previously offered every LLM topic phrase plus prefixes inferred from those phrases. It now offers only the requested broad groups: **AI, Gym, Food, Electronics**, and **Other** for unmatched subjects. Only groups present in the user's loaded library are shown. At most five topic choices appear; generated per-memory phrases and prefixes are no longer added to the dropdown or chips.

Known subject terms in the stored topics take precedence. When those topics do not identify a group, matching uses the displayed title/Brief, tags, tool/product entities, and content-type/category hints. Author/person entities, private user notes, and raw captions are not used for this fallback. The grouping is deterministic English/Arabic keyword matching, not a new LLM classifier. Specific names such as Claude remain available under Entity.

The backend uses the same aliases and priority as the offline client. A regression test checks alias parity. SQL values are bound; the existing user predicate applies before grouping and pagination. Legacy specific-topic query parameters remain supported for compatibility, but are not offered as new UI choices.

No stored topics, Briefs, users, schema, or provenance were changed. No LLM generation or data migration was run. Supabase/free-VM deployment is deferred to the final phase, as recorded in `CURRENT_PHASES.md`.

## Live evidence

The running Compose API and its existing three saved Claude-related reels were checked over HTTP:

| Topic | `/api/v1/items` count | `/api/v1/search?q=claude` count |
| --- | --- | --- |
| AI | 3 | 3 |
| Gym | 0 | 0 |
| Food | 0 | 0 |
| Electronics | 0 | 0 |
| Other | 0 | 0 |

All responses returned HTTP 200. The unfiltered library contained three records. AI matching all three is expected for this dataset; the other groups are not shown as available choices in that library.

Isolated database and mobile fixtures additionally cover recipes, workouts, electronics, an unrelated historical subject, differently worded Claude topics, title/Brief fallback, Arabic content, and another user's AI memory. Another user's item is excluded. A creator named Claude does not classify an unrelated historical subject as AI. Explicit Food metadata takes precedence over an incidental Claude mention in a title. Hostile topic input is treated as a literal value.

The widget regression checks that the dropdown contains only Any/AI/Food for its mixed fixture and excludes generated topic phrases. Removing the last Food memory and refreshing removes the Food choice. Existing pagination, filtered search, and content-type filtering tests pass.

## Commands and results

From `backend`:

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_intelligence_filters.py tests/test_phase12_hybrid.py tests/test_brief_search.py tests/test_phase16_multitenant.py -q
```

**81 passed.** After adding Arabic/title/Brief fallback assertions and aligning fallback fields with the mobile display:

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_intelligence_filters.py -q
```

**10 passed** on the final backend code. These counts overlap; do not add them.

From `mobile`:

```sh
flutter test test/intelligence_filters_test.dart test/library_ui_test.dart --timeout 30s --reporter expanded
flutter test --reporter expanded --concurrency=1
flutter analyze
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

Focused tests: **12 passed**. Full suite: **118 passed, 1 skipped**. Analyzer: **No issues found**. Debug APK: **built successfully**.

From the repository root:

```sh
git -c core.whitespace=cr-at-eol diff --check
```

**Passed.** No schema migration, worker/queue changes, or deployment cutover occurred. Physical-phone UI walkthrough: **NOT RUN**; automated widget tests verified the selector.

```sh
adb -s 192.168.1.8:38855 install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
```

Updated APK installation returned **Success**, preserving application data.

## Limits

- Subjects outside the four named groups appear under Other. Additional groups require a later explicit change.
- Keyword grouping has limits for ambiguous subjects or languages outside the configured aliases; it does not claim semantic classification accuracy beyond the fixtures and live checks above.
- Topic choices still scan the user's paginated library metadata. Server-side facets can be considered if library size makes this expensive.
- Individual accounts and production authentication remain a later phase; the local deployment still uses development authentication.
