# FindBack v6 phased implementation plan

Goal: complete the approved v6 design in the user's execution order.
Spec: ../specs/2026-10-09-findback-v6-design.md, amended by the user's approval.
Execution: inline; phase gates are mandatory. Stop after the Phase 1 report.

## Binding amendments

- Anonymous successful fetch/extraction establishes PUBLIC eligibility; never use
  credentials or cookies for global evidence. Login-walled results stay owner-scoped.
- First identify the phone build's actual API URL and reproduce with it. Verify
  Flutter sync/auth even if infrastructure is the initial failing boundary.
- Readiness must report API, worker, dispatcher, and beat availability.
- One configurable share expiry, default 30 days. Expiry/revocation never removes
  recipients' already-saved copies. Senders can view/revoke active links.
- Account-deletion dialog: "You are going to delete your account and remove all of your saved memories/cards."
- Sharing uses optional display name, never email; otherwise "Shared by a FindBack user".
- No phase starts before the preceding phase is verified. No skipped/loosened checks.

## Phase 0 — CI/CD

Files: .github/workflows/ci-cd.yml, backend/tests/conftest.py,
backend/tests/test_test_environment.py, and any failing boundary identified in logs.

- [x] Read latest GitHub failure and reproduce the failing test without removing checks.
- [ ] Fix the test-database configuration boundary: require TEST_DATABASE_URL from
  the process environment before loading local .env values.
- [ ] Verify safety regressions, full backend tests against a dedicated test DB,
  flutter analyze and flutter test, deployment regression, compilation, Compose
  configuration, and migration checks exercised by backend CI.
- [ ] Publish the phase commit to the feature branch and verify GitHub CI. Feature
  branch CI must not trigger master-only production deployment.
- [ ] Report changes, evidence, and any untested deployment behavior.

## Phase 1 — Processing

Files: build configuration/tool scripts, Flutter capture/sync/auth/API services,
backend health/ingest/outbox/tasks, Compose/deployment startup, and covering tests.

- [ ] Identify installed phone package/build, API URL, flavors, and launcher defines.
- [ ] Reproduce failure using that URL; trace API/auth/upload/job/worker boundaries.
- [ ] Write and run failing regression tests at each identified failing boundary.
- [ ] Fix root causes with durable offline retention and clear error states.
- [ ] Add readiness for worker/outbox/beat; verify recovery and startup configuration.
- [ ] Run full CI checks and GitHub CI; report root cause and phone API URL; STOP.

## Later phases (refine exact interfaces before starting each phase)

- [ ] Phase 2: URL normalization, anonymous-public classification, global sliding
  TTL/cache cleanup, concurrent processing, manual-regeneration isolation/limits,
  conditional feed requests, migrations and backend regression tests.
- [ ] Phase 3: exact palette tokens, shared anchored actions/dialogs/toasts,
  confirmation opt-out/reset, silent refresh, status mapping, SQLite migrations.
- [ ] Phase 4: all requested Library/Memory/Collections/Account screen changes.
- [ ] Phase 5: immutable share snapshots, native links/fallback/verification,
  sign-in continuation/redemption, list/revoke shares, display name.
- [ ] Phase 6: visual comparison in both themes/narrow widths, all tests and CI,
  docs/env examples, final report and manual device checklist.

## Review focus

Explicit test DB absence even when .env supplies one; no destructive test access
to developer DBs; offline captures never expire; server errors never masquerade as
device offline; feature-branch verification never deploys production.
