# Phase 7: optional accounts and top-bar status

Verified on October 7, 2026. Supabase provides authentication in this phase. The API, Celery, Redis, and PostgreSQL still run locally; this is not the VM or Supabase database deployment.

## Implemented and checked

- Startup restores secure credentials and opens SQLite without a network login gate. Guests can save, search, filter, open, and share memories.
- The rightmost account icon opens optional email/password sign-in, signup, and password reset. Input validation, wrong credentials, confirmation-required signup, masked duplicate email responses, refresh failures, and verified recovery links have mocked-provider coverage.
- Signed-in requests use Supabase ES256 tokens verified against the project issuer, audience, expiry, and JWKS. Guest processing uses separately issued, expiring tokens. Shared development authentication is disabled in the running environment.
- Guest Briefs are cached locally before temporary server saves are deleted. Expired unfinished staging is retried from the device's retained URL. Account caches and queues use separate database files; logout opens a new guest scope. Guest-to-account import preserves the original cache and queues URLs through the existing authenticated sync path.
- Queue and processing slots are independent. The oldest active slot is nearest the fixed account icon. Inactive slots occupy no space.
- The refresh cue hides after three seconds and when leaving the top; returning to the top shows it again. Pull-to-refresh remains available with the cue hidden.

## Commands and evidence

Backend (from `backend`, isolated test database only):

```bash
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test .venv/bin/python -m pytest tests/test_phase7_accounts.py tests/test_phase16_multitenant.py tests/test_auth_http.py tests/test_phase5_outbox.py tests/test_outbox_wiring.py tests/test_phase14_gateway.py -q
```

Observed: **90 passed, 24 warnings in 36.44s**. The empty Supabase URL applies only to legacy local-token compatibility tests; production retains strict Supabase verification.

Mobile (from `mobile`):

```bash
timeout 120s flutter test --timeout 30s --reporter expanded
python test/android_share_test.py
flutter analyze
```

Observed full Flutter run: **147 passed, 1 skipped, no failures**. This includes guest-to-A-to-B-to-logout-to-A cache/token isolation, preserved guest imports, cache-before-delete, expired staging, sync shutdown, slot ordering, and refresh timing. Native Kotlin checks passed for share buffering, pause/resume, recovery routing, and manifest registration. Analyzer: no issues found. After adding the paused-listener shutdown regression, `flutter test test/auth_test.dart --reporter expanded` passed **13 tests**, and the final analyzer again reported no issues.

A debug APK built using `API_BASE_URL=http://127.0.0.1:8000` and only the public Supabase URL/publishable key. Installed with `adb install -r`; port 8000 is forwarded through ADB. Phone checks showed cached memories, the account icon, the optional account form, and the hidden refresh hint. No real signup, login, reset email, or second-device account synchronization was performed: no user credentials were provided.

Live HTTP checks against the running API confirmed:

- Missing authentication returns 401.
- Two independently issued guest sessions initially have empty server libraries.
- A guest cannot read the preexisting account records; another guest cannot read the first guest's newly staged save (404).
- The owning guest can read its staged save; that test save was deleted afterward.
- Public Supabase JWKS reports ES256; the email provider is enabled and signup is enabled.

The test save was still processing when inspected. This live check proves authorization, not a newly generated real LLM Brief or phone cache-before-delete completion.

## Data preservation and migration

Before the runtime authentication switch, PostgreSQL and Android databases/preferences were backed up to `~/.local/state/findback/backups/phase7-before-accounts/` (`database.dump`, `device.tar`). The three preexisting saved reel IDs remained present and ready. No schema migration was performed; the production revision remains **0013_job_attempt_token**. No old shared-development records were reassigned to the first account.

## Required manual configuration and limits

Allow `findback://auth/recovery` in Supabase Authentication → URL Configuration → Redirect URLs. Email delivery and confirmation policies are managed by the Supabase project. The application supports confirmation-required signup without blocking guest use.

Guest data is device-local after processing; clearing app data or uninstalling can lose it. Accounts provide permanent server-backed storage, but remote access outside the home still requires the later VM deployment. Supabase authentication alone does not expose the local API remotely.

Guest-token issuance currently has no dedicated rate limit, and expired guest identity rows are retained. These are deferred operational-hardening observations for Phase 8. Supabase database migration and the free VM remain Phase 9.
