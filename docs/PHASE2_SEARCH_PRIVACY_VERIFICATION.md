# Search, filters, and privacy verification — 2026-10-07

## Verified causes and changes

- Production search admitted every vector neighbor regardless of similarity. On the three saved coding reels, `claude` scored 0.639–0.684; `banana bread recipe` scored 0.475–0.505; `zzzxqvnotamemory` scored 0.548–0.568. Vector-only results now require `SEARCH_MIN_COSINE` (default 0.60). Textual matches remain eligible, and fusion ranks are preserved.
- Entity/topic matching formerly required an exact whole label. Literal, word-bounded phrase matching now groups `Claude`, `Claude Security`, and `Claude Code Setup`; `Clau` does not match. Entity choices exclude numbers and unknown fields. Online PostgreSQL and offline Dart use equivalent phrase boundaries.
- Topic choices now include prefixes shared by multiple distinct saved items, alongside specific topics. For example, `AI coding` and `AI writing` expose an `AI` group. This is grouping existing metadata, not an inferred topic ontology.
- Changed/cleared searches invalidate older requests. Previous lists are cleared while new searches or library filters load.
- Offline SQLite searches escape `%`, `_`, and backslashes as literal text and continue binding query values.
- Facebook's `Moaz sent you a reel` was in a stored caption. The cleaner now strips recipient notifications and the standalone Reels navigation label. Both model input and written-source grounding use cleaned title/caption text.

## Live API/database checks

The running Compose API was checked over HTTP against its real database:

| Request | HTTP status | Item count |
| --- | --- | --- |
| Library, entity `Claude` | 200 | 3 |
| Library, topic `AI` | 200 | 3 |
| Library, nonexistent topic | 200 | 0 |
| Search `claude` | 200 | 3 |
| Search `banana bread recipe` | 200 | 0 |
| Search `zzzxqvnotamemory` | 200 | 0 |
| Search `' OR 1=1; DROP TABLE items; --` | 200 | 2 |

The hostile string was processed as search text, not executable SQL. It can produce semantic coding matches. The database still contained all three records afterward. ORM filters and raw search statements bind query/category values; filter JSON keys and paths are allowlisted. The existing SQL ID allowlist accepts database-derived UUIDs parsed through `UUID`, not arbitrary filter strings. This is focused verification, not a claim that every application vulnerability was audited.

Before metadata repair, a PostgreSQL custom-format dump was created and checked with `pg_restore --list`:

`/home/moaz/.local/state/findback/backups/phase2-entity-grounding-before.dump`

The backup is 91,118 bytes and has mode 0600. Rollback is available from this pre-repair dump; no rollback was performed.

The existing `drop_unconfirmed_names` rule, using cleaned written evidence, removed only `Moaz` from item `7c8e9b90-10b8-4bb8-a307-e44180a9452b`. Item entities, Brief entities/tags, and derived search text were synchronized in one transaction. The source evidence was preserved, and an assertion checked that other Brief fields were unchanged. No new LLM generation was run. After repair, the database showed `people_orgs=[]`, `brief_source=llm`, `prompt_version=brief_v3.3`, and `status=ready`. The owner's record count remained three.

## Test commands and results

From `backend`:

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_intelligence_filters.py tests/test_phase12_hybrid.py tests/test_brief_search.py tests/test_phase16_multitenant.py tests/test_auth_http.py tests/test_source_cleaning.py -q
```

**96 passed, 1 warning**. The warning is python-jose's deprecated `datetime.utcnow()`. This run preceded the additional model-input/grounding cleaning regression below.

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_source_cleaning.py tests/test_brief_integrity.py tests/test_brief_v2.py -q
```

**35 passed** on the final grounding changes. Counts overlap and must not be added. Tests use isolated databases, not the production database.

The older search HTTP test expected a headphones query restricted to recipes to return a recipe. Its expectation now explicitly requires no results; a category restricts relevance instead of creating relevance. Existing category-before-limit tests and ranking tests still pass.

From `mobile`:

```sh
flutter test test/intelligence_filters_test.dart test/search_controller_test.dart test/library_ui_test.dart --reporter expanded
flutter test --reporter expanded --concurrency=1
flutter analyze
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

Focused tests: **19 passed**. Full suite: **117 passed, 1 skipped**. Analyzer: **No issues found**. Debug APK: **built successfully**.

```sh
adb -s 192.168.1.8:38855 install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
adb -s 192.168.1.8:38855 reverse tcp:8000 tcp:8000
git -c core.whitespace=cr-at-eol diff --check
```

APK install returned **Success**, forwarding completed, and diff check passed. No automated physical-phone UI walkthrough was performed. The worker was restarted when its jobs were only READY/FAILED, to load caption cleaning; API/outbox/PostgreSQL/Redis/worker were running afterward. No schema migration was performed.

## Open account-isolation dependency

The running API still has `DEV_AUTH_ENABLED=true`. Unauthenticated installations therefore share one development account. Authenticated two-user isolation regression tests pass, but that does not make the development deployment safe for separate users.

Neither `SUPABASE_URL` nor `SUPABASE_JWT_SECRET` is configured in the API. Individual account setup awaits Supabase project details. No account UI, cloud migration, or authentication cutover was performed, and existing data was not reassigned to a guessed identity.

The similarity cutoff was verified against this three-item live dataset and deterministic regression cases; a larger corpus/provider change may require recalibration. Runtime-wide optimization, general log cleanup, and the remaining UI enhancements are outside this stage.
