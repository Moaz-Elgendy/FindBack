# Public testing implementation plan

**Goal:** Improve launch safety, video briefs, and a simple private Collections experience.
**Architecture:** Retain Flutter, FastAPI, PostgreSQL, Celery, Redis and existing AI providers. Reuse the library cards and content identity. Collections belong to one user; guests keep them in SQLite. Limits are enforced across workers using Redis.
**Spec:** Approved phase order and UI direction in the conversation; findings in docs/LAUNCH_AUDIT.md.
**Execution:** Native in this session. User explicitly approved all phases without intermediate approval gates.

## Constraints
- Preserve optional authentication, guest offline use, existing saves and briefs.
- No automatic movement into suggested collections; user controls manual collections.
- Resolve shared links safely before video classification; do not claim support for inaccessible/DRM content.
- Keep blue accent, prominent briefs, hidden tags, processing border, account/status slots and unobtrusive refresh.
- No production pytest, secret disclosure, destructive data cleanup, or Git push.

## Phase 2: Launch safety and capacity
Files: requirements.txt; URL safety utility; existing ingestion/fetch/media paths; shared Redis budgets, AI retries, guest authentication; focused tests and .env.example.
Schema: none. API: reject unsafe URLs; return 429 + Retry-After for intake limits, 503 when enforcement is unavailable. Workers retain jobs during provider budget/circuit pauses.
Risks: dependency compatibility, safe redirect enforcement without breaking shared links, quota starvation.
- [ ] Regress unsafe URLs, distributed quota atomicity, retry deferral, and provider pause behavior.
- [ ] Implement shared request/token budgets and intake quotas; upgrade vulnerable dependency group.
- [ ] Run focused tests and backend suite; audit dependencies again.

## Phase 3: Video quality
Files: fetcher, media_understanding, pipeline, brief validators, media/grounding regression tests.
Schema: none. API: existing upload/capture surfaces retained.
- [ ] Reproduce tt.site alias bypass and boilerplate-only finalization.
- [ ] Safely resolve redirects and classify video using final URL/media metadata.
- [ ] Keep low-evidence navigation wrappers out of final briefs; assess supplied TikTok with production prompt/provider/validation.
- [ ] Verify canonical identity, timestamps, supported names and fallback handling.

## Phase 4: UI and Collections
Files: Flutter app/home/card/detail; collections model/service/pages; local_db and account transitions; API collections router/models/migration; widget and API isolation tests.
Schema: owned collections + owned memory membership; SQLite equivalent. Migration is additive and reversible.
API: list/create/update/delete collections; add/remove own saved memories; suggestions derived from supported shared entities/topics.
- [x] Tests: two-user isolation, duplicate membership, deletion preserves memory, guest-to-account transfer, offline collection persistence.
- [x] Implement Library/Collections bottom navigation with cover grids and counts.
- [x] Refine spacing, typography, color hierarchy and brief/detail presentation with existing blue accent.
- [x] Test text scaling, dark/light modes, empty/processing states in widgets.
- [ ] Phone UI verification deferred to the release phase.

## Phase 5: Release verification
- [x] Full backend and Flutter tests/analyze, deployment checks and dependency audit.
- [x] Real-LLM and safe platform acquisition checks; TikTok visual analysis blocked by Gemini quota, documented.
- [ ] Controlled load/isolation checks against test infrastructure only.
- [x] Build/deploy verified backend and install mobile debug build on available device.
- [x] Record results and limits in docs/PHASE_5_RELEASE_VERIFICATION.md.

Hosted smoke verified isolation and automatic READY completion using disposable
guest records, then removed them. Production load testing was not performed.
Phone Library was visually checked; Collections interactions and mobile-data
connectivity remain unverified.
