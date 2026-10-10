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
- [x] Fix the test-database configuration boundary: require TEST_DATABASE_URL from
  the process environment before loading local .env values.
- [x] Verify safety regressions, full backend tests against a dedicated test DB,
  flutter analyze and flutter test, deployment regression, compilation, Compose
  configuration, and migration checks exercised by backend CI.
- [x] Publish the phase commit to the feature branch and verify GitHub CI. Feature
  branch CI must not trigger master-only production deployment.
- [x] Report changes, evidence, and any untested deployment behavior.

## Phase 1 — Processing

Files: build configuration/tool scripts, Flutter capture/sync/auth/API services,
backend health/ingest/outbox/tasks, Compose/deployment startup, and covering tests.

- [x] Identify installed phone package/build, API URL, flavors, and launcher defines.
- [x] Reproduce failure using that URL; trace API/auth/upload/job/worker boundaries.
- [x] Write and run failing regression tests at each identified failing boundary.
- [x] Fix root causes with durable offline retention and clear error states.
- [x] Add readiness for worker/outbox/beat; verify recovery and startup configuration.
- [x] Run full CI checks and GitHub CI; report root cause and phone API URL; STOP.

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


## Phase 0 verification

Commit `500ab8b`; GitHub run https://github.com/Moaz-Elgendy/FindBack/actions/runs/37986965261 passed. Backend baseline 1068 passed/1 existing skip; Flutter 432 passed/1 existing skip; analyze, Docker build, Compose, migration checks and deployment regressions passed. No production deployment.

## Phase 1 investigation and verification

The installed debug APK matched the original local build exactly and compiled `http://localhost:8000`. There were no flavors or hosted URL override, and no ADB reverse. Local API/worker/dispatcher/beat were stopped although PostgreSQL and Redis were running. Starting the API also exposed an unmigrated local database; guest creation failed with 500 and then 429. Flutter misclassified those responses as connectivity failures and repeated guest lease requests. Guest tokens were not scoped by backend origin, so switching endpoints could reuse a foreign lease.

Fixed startup migration gates, schema and processing readiness, release-scoped heartbeats, data-preserving rollback, guest error classification/cooldown/origin-scoped leases, shared capture/sync Retry-After deadlines, and visible backend failure messages. Normal builds now default to `https://findback.duckdns.org`; explicit local dart defines remain supported. No saved capture expiry was introduced.

Local database backup: `/home/moaz/.local/state/findback/backups/phase1-2026-10-09/local-before-migrations.dump`. Before stamping the previously unversioned database at 0013, compared its schema to a disposable database migrated to that revision (columns, constraints, indexes and defaults matched). Upgraded to 0025 without resetting data. ADB reverse enabled for reproducing the original local configuration. Worker-stop experiment made `/ready` return 503 after heartbeat expiry and return 200 following service recovery.

Full backend verification before the final review fixes: 1078 passed, 1 existing skip. Final readiness regressions: 9 passed. Deployment rollback and launcher regressions pass. Final Flutter full suite: 441 passed, 1 existing skip; analyze clean and debug APK built; legacy-token migration cases and guest API regressions: 6 passed. Valid legacy guest tokens are authenticated by the selected backend before adopting the origin-scoped key, preserving existing guest ownership. Final phone verification and GitHub CI are reported at the phase checkpoint. Phases 2–6 remain unstarted.

GitHub run 37991227540 passed mobile but Docker Hub rejected the backend service image before checkout (unauthenticated pull rate limit). CI services now use Google’s public Docker Hub cache for the same pgvector/PostgreSQL 16 and Redis 7 Alpine images; both image manifests were checked. No tests or checks were removed or weakened.


## Phase 1 checkpoint

GitHub run https://github.com/Moaz-Elgendy/FindBack/actions/runs/37991664998 passed (1080 backend / 441 Flutter tests; one existing skip each). Original phone used localhost; repaired local startup and reverse mapping resulted in all three backend saves reaching ready. Final APK defaults to deployed HTTPS and was installed preserving data. Phone SQLite had no local-only items or pending upload rows. Production changes were not deployed; a fresh save through the final APK remains a manual device check.

## Phase 2 implementation sequence

1. Conservative normalization and legacy lookup: use existing platform identity; never rewrite or collapse personal saves during migration.
2. Expand ContentAsset with nullable cache URL, last-hit, expiry and machine-result snapshot; unique cache URL applies only to positively verified entries. Store derived output and search chunks independently of personal items so deletion of the original save does not destroy reusable output.
3. Anonymous worker classification with no preview/hints, identity, cookies or owner evidence. Serialize by normalized URL using a PostgreSQL advisory lock held on the same connection used by the processing session through its commits, avoiding a second pool slot. A competing owner-scoped save may copy a completed global result without moving its personal memory metadata. Unknown/login-walled results stay owner-scoped. Ingest stays nonblocking and can immediately copy an already-completed usable cache result.
4. Sliding 30-day TTL checked on every hit; bounded cleanup on the existing beat removes global payload only. Existing personal briefs/chunks/reminders are retained.
5. Manual regeneration detaches the requesting memory into its own asset/job, bypasses shared output and enforces an atomic configurable default limit of three per memory per UTC day. Previous brief restoration stays intact.
6. Feed ETags are computed from the exact filtered/paginated response and scoped by account; unchanged requests return 304.
7. Prove privacy, concurrent processing once, TTL/cleanup, original-save deletion, failure retry, manual isolation/limits, conditional feeds and reversible migration. Run all suites, Compose/Docker checks and GitHub CI before Phase 3.

Ruling: classify in the worker, not by blocking ingestion on an anonymous network probe — preserves the existing fast-save contract and durable queue while allowing common public links to deduplicate processing.
