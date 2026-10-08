# Phase (e) report

- Files changed: Account screen, weekly settings model, API client, app scope wiring, account coordinator and local cache clearing; new backend Account router/service, preferences/deleted-identity models, identity guard, snapshot storage fencing and migration `0019_account_settings`; environment template, Account tests, affected Appearance/coordinator tests, migration-head assertions and authentication test fixture session cleanup. Supporting documents: `ACCOUNT_PHASE_PLAN.md`, `ACCOUNT_API_CONTRACT.md`, and the Account row of `ADDENDUM_AUDIT.md`.
- Database/schema changes: PostgreSQL `weekly_note_preferences` and `deleted_identities`; SQLite remains version 6. Only a subject digest remains for deleted-token protection.
- API changes: Owner-scoped weekly preference GET/PUT, save export GET and account DELETE. See `ACCOUNT_API_CONTRACT.md`.
- Worker/queue changes: No new worker or queue. Snapshot writes take the same owner lock as deletion and verify item existence, preventing retained raw content from late fetches.
- Tests added or modified: Backend Account ownership/preferences/export/provider/error/deletion/storage-concurrency tests; Flutter Account API, settings/permission/export/account-switch/deletion/layout tests; existing theme tests scroll to controls; coordinator tests cover failed/successful deletion and isolated caches; migration-head assertions advanced to 0019; authentication test fixture now closes its user-query session.
- Exact test commands run:
  - `SUPABASE_URL= TEST_DATABASE_URL=postgresql://findback:findback@127.0.0.1:55433/fb_account_validation .venv/bin/python -m pytest -q`
  - Same environment with `tests/test_account.py tests/test_phase16_identity.py -q` and `tests/test_account.py tests/test_phase16_migration.py tests/test_auth_http.py -q`.
  - Same environment with `tests/test_account.py tests/test_phase16_multitenant.py tests/test_undo_delete.py::test_migration_rollback_preserves_pending_save_data -q` for fixture cleanup verification.
  - `flutter test --reporter expanded`
  - `flutter test test/account_redesign_test.dart test/account_coordinator_test.dart --dart-define=CAPTURE_REDESIGN=true --reporter expanded`
  - Focused Flutter Account/authentication/theme/API test runs.
  - `flutter analyze`
  - `flutter build apk --debug`
  - `git diff --check`
  - `python3 -m compileall -q backend/app backend/alembic/versions/0019_account_settings.py`
- Test results: Full backend **900 passed, 1 skipped**. Full Flutter **307 passed, 1 skipped**. Focused Account/layout/coordinator capture **33 passed**; backend Account/storage concurrency **9 passed**; backend migration/auth regression **26 passed**. Final authentication-fixture/migration regression **54 passed**. Analysis: **No issues found**. Android debug APK built successfully. Diff and compilation checks passed. An earlier superseded full backend run was interrupted after a migration waited on an unclosed authentication test session; its counts are not reported as a successful run. The fixture now closes that session without changing assertions.
- Migrations performed: Migration chain tested in isolated validation databases only. No application database migration, commit or deployment.
- Known limitations / remaining issues: Weekly delivery is intentionally phase (f). Deletion requires configured Supabase admin credentials and raw-storage deletion access when storage is configured. Provider, storage and database deletion cannot be atomic; retry/operator limits are documented in `ACCOUNT_API_CONTRACT.md`. iOS build, physical-device permission/share/deletion and screen-reader checks were NOT RUN on this Linux host. No real external authentication identity or storage object was deleted during tests.
- Deferred observations: Account deletion cannot discover historical raw objects whose item rows were already permanently purged. Weekly snapshots and opened-item tracking remain phase (f).
- How this matches the phase requirements (max 5 lines): Account now groups identity, weekly preferences, Appearance and Your data using existing tokens. Existing guest/sign-in/signup/password recovery flows are retained. Export includes real edits and queued local saves. Permanent deletion is confirmed and scoped, and asynchronous actions abort on account changes. Collections UI is untouched.

## Visual and design decisions

Inspected Account captures at 360dp and 390dp in both themes with bundled fonts. Widget tests cover 320/360/390dp, text scales 1/1.3/2, RTL and large-text guest validation. Appearance choices wrap instead of using a fixed-width segmented control so labels remain usable at large text. Settings remain scrollable. Card groups use zero elevation with the soft border token. Sunday 6:00 pm is the default schedule; weekly opt-in defaults off. Native sharing exports JSON with versioned fields. No old "When a reminder fires" group was introduced.

Independent read-only review identified and verified fixes for late raw uploads, stale settings loads and cross-account export mixing. The reviewer did not run tests independently.
