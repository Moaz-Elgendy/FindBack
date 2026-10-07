# Phase 4 verification

Verified 2026-10-07 against the local Compose database and connected Android phone.

## Scope

Memory cards and detail pages use the existing primary color for titles. The saved date uses Flutter's localized short-date formatting in the local timezone; missing dates are omitted. Metadata wraps without changing the layout. Widget checks cover narrow cards, 2× text, light/dark themes, and title contrast of at least 4.5.

Topics now use semantic labels emitted in the existing Brief generation call, rather than matching incidental keywords. The shared catalogue covers AI, Programming, Gym, Food, Electronics, Design, Business, Education, Science, History, Travel, Health, Finance, Entertainment, Lifestyle, and Culture. Production v2 validation accepts at most two supported labels and uses the existing repair/fallback path for invalid labels. The production prompt version is brief_v3.4. The legacy extraction prompt receives the same topic instructions. Online and offline filters match stored labels; neither infers a topic from a person's name, title keywords, or tags. Other is no longer offered. No separate classification call is added to new saves.

Legacy topic projection is checked against both the item response schema and the database filter. Existing Brief fixtures were updated from phrase tags to the new semantic-topic contract; their evidence and Brief bodies were preserved. A regression explicitly verifies rejection and repair of a per-memory topic phrase.

## Real database and LLM evidence

Before editing existing topic metadata, a PostgreSQL custom-format backup was created with exclusive creation and permissions 0600:

`/home/moaz/.local/state/findback/backups/phase4-semantic-topics-before.dump` (91,109 bytes). `pg_restore --list` succeeded.

The existing gateway classified the stored titles, short Briefs, and key points in one batch. Gemini returned 503, then two read timeouts, exhausted three attempts, and failed over to Groq `openai/gpt-oss-120b`. The real response assigned AI and Programming to each of the three existing reels. Only `brief_v2.topics` was persisted. All other Brief JSON fields were compared before/after and preserved. This was a topic metadata repair, not full pipeline regeneration. Existing Brief provenance was not manually changed; new pipeline Briefs receive brief_v3.4 normally.

[Real LLM response and provider usage](ui-verification/phase4/semantic-topics.json).

[Live HTTP evidence](ui-verification/phase4/live-api.json): both `/api/v1/items` and `/api/v1/search?q=claude` returned HTTP 200 and all three reels for AI and Programming. Design, Food, History, Science, and Other returned zero. History/Science filtering and isolation are covered by fixtures, not real historical/scientific memories in this three-record dataset.

Database migration version remained `0013_job_attempt_token`. No schema migration, user ownership, worker, queue, or infrastructure change occurred.

## Exact checks

From `backend`:

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_intelligence_filters.py tests/test_brief_v2.py tests/test_brief_integrity.py tests/test_brief_worker.py tests/test_extractor.py tests/test_phase12_hybrid.py tests/test_phase16_multitenant.py tests/test_brief_search.py -q
```

**118 passed in 53.88s**, exit 0. After the result, SQLAlchemy logged a pool-reset error (`psycopg2.OperationalError: server closed the connection unexpectedly`) during test database cleanup. This was not a test failure; it is recorded for the later runtime audit.

Additional legacy-topic check, from repository root:

```sh
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test backend/.venv/bin/python -m pytest backend/tests/test_intelligence_filters.py -q
```

**12 passed in 3.74s.**

From `mobile`:

```sh
flutter test --reporter expanded
flutter analyze
flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

**120 passed, 1 skipped**; analyzer **No issues found**; APK build succeeded.

From repository root:

```sh
adb -s 192.168.1.8:38855 install -r mobile/build/app/outputs/flutter-apk/app-debug.apk
adb -s 192.168.1.8:38855 reverse tcp:8000 tcp:8000
git -c core.whitespace=cr-at-eol diff --check
```

Installation returned **Success**, preserving app data. Reverse command succeeded. Diff check exited 0. A physical visual walkthrough was NOT RUN; installation and widget tests do not establish one.

## Limits and deferred work

Semantic classification can still be imperfect. Unsupported or evidence-free subjects are left unclassified, rather than guessed into Other. The catalogue is finite; no open-ended topic proliferation is allowed. Legacy unsupported phrases are hidden by the selector until classified; the three current memories have been classified. Individual accounts remain Phase 7; unauthenticated development clients still share the dev account. Topic choices continue to use the loaded paginated library. No live full-Brief generation reliability evaluation was run in this phase.

Phase 5 is processing-border animation and refresh UX. Free-VM/Supabase deployment remains the final phase. Runtime/test-cleanup warning investigation belongs to Phase 8.
