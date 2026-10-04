# Phase 1 — Real-provider validation report (report-only pass)

Nothing in this pass changed product code. Every finding below was observed
against the running Docker Compose stack with the real Gemini provider from
`.env`. No key values are printed anywhere in this report.

---

## 1. Environment and services

- Stack: 5 containers (postgres, redis, api, worker, outbox) up; `GET /health`
  → `status:ok, db:ok, redis:ok`.
- AI config as loaded by the app: chat = `gemini:gemini-3.8-flash`,
  embed = `gemini:gemini-embedding-001@1536d`.
- Free-tier quota (observed via the API's own error body, no key printed):
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, **20 requests/day per
  model** for `gemini-3.8-flash`; during this pass Google reported
  "Please retry in 17h33m". Embedding quota is separate and never failed.

## 2. Links vs outcomes (22 saves)

"Extraction" = whether the AI JSON brief validated on the first try or fell
back to the heuristic brief. "Fetch source" = which leg of the fetch chain
(YouTube transcript → Firecrawl → Jina Reader → preview) actually produced
page text.

### Batch A — the nine required link types, saved one at a time (Oct 3)

| # | Link | Type | Status | To ready | Extraction | Brief (title / overview / profile) | Fetch source | Errors seen |
|---|------|------|--------|----------|------------|------------------------------------|--------------|-------------|
| 1 | wikipedia Portal:Current_events | news | ready | 9s | **AI, 1st try** | "Wikipedia Portal: Current Events" / "A link to Wikipedia's Current Events portal, which documen…" / general | none (preview fallback) | — |
| 2 | martinfowler.com/articles/microservices | blog | ready | 13s | **AI, 1st try** | "Microservices Article by Marti…" / "A link to Martin Fowler's foundational article…" / general | none | — |
| 3 | wikibooks Cookbook:Mushroom_Risotto | recipe | ready | 14s | **AI, 1st try** | "Mushroom Risotto Recipe" / "A link to a Wikibooks cookbook entry providing a recipe…" / recipe | none | — |
| 4 | wikipedia IPhone_16 | product | ready | 28s | heuristic (fallback) | "iPhone 16 Product Specs" / "Link: https://en." / general | none | Gemini **503** on every retry (longest save) |
| 5 | youtube.com/watch?v=dQw4w9WgXcQ | video | ready | 7s | heuristic (fallback) | "Never Gonna Give You Up video" / "Link: https://www." / list | none (transcript fetch also failed silently) | Gemini **429** |
| 6 | tiktok.com/@tiktok/video/7106594312292453678 | social | ready | 4s | heuristic (fallback) | "TikTok viral dance video" / "Link: https://www." / general | none | **429** |
| 7 | instagram.com/p/C-example123/ | social | ready | 4s | heuristic (fallback) | "Instagram public photo post" / "Link: https://www." / general | none | **429** |
| 8 | facebook.com/facebook/posts/10158499999999999 | social | ready | 4s | heuristic (fallback) | "Facebook public community upda…" / "Link: https://www." / general | none | **429** |
| 9 | x.com/OpenAI/status/1800000000000000000 | social | ready | 4s | heuristic (fallback) | "OpenAI announcement on X" / "Link: https://x." / general | none | **429** |

### Deliberate bad cases (Oct 3) — none hang; each ends `ready` (limited)

| Bad case | Status | To terminal state | Extraction | Errors seen | Notes |
|----------|--------|-------------------|------------|-------------|-------|
| Unreachable URL (`this-domain-does-not-exist-123456789.org`) | ready (limited) | 4s | heuristic (429) | 429 | Bounded, no hang. Caveat: see §7-7 — the fetch chain dies before ever contacting the target host, so target-DNS handling itself was not exercised. |
| Private post (`facebook.com/groups/privategroup123456/…`) | ready (limited) | 4s | heuristic (429) | 429 | No login-wall path exercised (never reached Facebook); outcome still correct and bounded. |
| Very long page (`wikipedia List_of_HTTP_status_codes`) | ready (limited) | 4s | heuristic (429) | 429 | Truncation limits not exercised in practice (zero bytes fetched). |

### Batch B — supplemental coverage (Oct 4)

| Link | Type | Status | To ready | Extraction | Brief overview / profile | Fetch source | Errors |
|------|------|--------|----------|------------|--------------------------|--------------|--------|
| wikipedia Mushroom_risotto | recipe/news | **pending — STUCK (permanent)** | never (observed >50 min) | never ran | (empty) | — | claim race, §5 |
| blog.golang.org/go-concurrency | blog | ready | 8s | heuristic (429) | "Link: https://blog." / general | none | 429 |
| allrecipes.com/recipe/233457/creamy-mushroom-chicken | recipe | ready | 12s | heuristic (429) | "Link: https://www." / recipe | none | 429 |
| amazon.com/dp/B07VJ4K6LN | product | ready | 11s | heuristic (429) | "Link: https://www." / general | none | 429 |
| youtube.com/watch?v=YE7VZl3uMGE | video | ready | 14s | heuristic (429) | "Link: https://www." / list | none | 429 |
| instagram.com/p/Ck-1234567890/ | social | ready | 11s | heuristic (429) | "Link: https://www." / general | none | 429 |
| facebook.com/kernel/posts/1234567890 | social | ready | 11s | heuristic (429) | "Link: https://www." / general | none | 429 |
| x.com/mathiasbynens/status/1700000000000000000 | social | ready | 9s | heuristic (429) | "Link: https://x." / general | none | 429 |
| aws.amazon.com/blogs/compute/…auto-scaling | news/tutorial | ready | 6s | heuristic (429) | "Link: https://aws." / tutorial | none | 429 |
| blog.openai.com/introducing-chatgpt | blog | ready | 4s | heuristic (429) | "Link: https://blog." / general | none | 429 |

Batch B was saved in bulk (7 at once) rather than one at a time; batch A above
is the strict one-at-a-time set. Titles come from save-time hints; profiles
(`content_type`) come from the URL/text profile classifier, which kept working
(recipe/list/tutorial) even when the brief was heuristic.

**Totals: 22 saves → 21 ready, 1 permanently stuck. Extraction: 3 AI first-try,
18 heuristic fallback (17 × 429 quota, 1 × 503 high-demand), 1 never ran.
Page text fetched: 0 of 22** — every save followed Firecrawl 401 → Jina dead →
empty preview → `"Link: <url>"`. `raw_preview` is empty for all 22 items (the
save payloads carried no preview), so even the three model-written briefs were
written from URL + title hint only (plausible because these are famous pages,
but not grounded in fetched content).

### Fetch-chain root cause (verified this pass, from inside the api container)

- Firecrawl: `.env` ships a placeholder `FIRECRAWL_API_KEY=fc-…` → **401 on
  every save** (21 calls in worker logs, wasted ~1–3s per save).
- Jina Reader: `fetcher.py` calls **`https://cc.jina.ai/…`** → direct probe:
  `ConnectError [Errno -2] Name or service not known` (DNS does not resolve).
  The current host **`https://r.jina.ai/…` returns 200 with real content**
  (1081 bytes for example.com, enough to satisfy the fetcher's own
  `len(text) > 200` check).
- The failure is swallowed by `except Exception: pass` with **no log line**, so
  the cause is invisible in worker logs; only a direct probe exposes it.
- YouTube: transcript fetch returned nothing and the fallback oEmbed/thumbnail
  path never ran (no `youtube.com` request in logs) → fell through to preview.

## 3. Search evaluation — real embeddings vs offline numbers

The offline numbers (0.722 / 0.944 / 0.819) come from a `provider: oracle` run
of the Phase 19 suite. Two runs were executed this pass:

| Metric | Offline (docs, oracle) | Oracle re-run (this pass) | **Real Gemini provider (this pass)** |
|---|---|---|---|
| Recall@1 | 0.722 | **0.722 (exact match)** | **0.778** |
| Recall@5 | 0.944 | **0.944 (exact match)** | **1.000** |
| MRR | 0.819 | **0.819 (exact match)** | **0.875** |
| Brief groundedness | 1.000 (141 stmts) | 1.000 (141) | 1.000 (41 stmts) |
| Brief usefulness | 1.000 | 1.000 | 0.791 |
| Extraction correct | 8/13 | 8/13 | 8/13 |
| Explanations valid | 32/35 | 32/35 | 20/29 |
| Semantic-only matches | 145 | 145 | 151 |

- The provider run is the **first provider-backed run of this suite**
  (`docs/EVALUATION.md` stated none had been done). All 13 corpus documents
  and all 18 queries were embedded with **real `gemini-embedding-001`** (every
  embed call returned 200); retrieval numbers are measured against real
  embeddings and beat the oracle on all three metrics.
- The oracle run reproduces the documented baseline exactly, which validates
  the comparison.
- The Japanese query (`静的サイトのビルドが速いツール`) that never retrieved its
  gold document under the oracle **now resolves** — the provider run has no
  "gold never returned" entry, which is what lifts Recall@5 to 1.000. This
  confirms EVALUATION.md's prediction that a real multilingual embedding would
  fix it.
- **Caveat (stated, not hidden):** at run time the daily chat quota was
  exhausted, so `UNDERSTAND` fell back to heuristic briefs during corpus
  ingest. Brief quality numbers (usefulness 0.791, 41 groundedness statements)
  therefore describe the *fallback*, not the model. Retrieval — the part the
  task asks about ("against the real embeddings") — is unaffected: embeddings
  all succeeded with the real provider.
- Why pytest cannot produce this run: `tests/conftest.py` strips all AI keys
  from every test by design ("no test may reach a real AI provider"), so any
  pytest run is necessarily `provider: oracle`. The provider run therefore
  called `evals.runner.run_evaluation` directly (scratch script, deleted after).

## 4. Dedupe, H3 reuse, and notes

| Check | Result |
|---|---|
| Dedupe — same user saves the same link twice | **Pass.** Second save returns the *same item* (Allrecipes `739bff73…`), no reprocessing, `dedupe_hits` recorded. |
| Note set/read — `GET/PATCH /api/v1/memories/{content_id}` | **Pass.** Test note written, read back, reset to empty (earlier this session). |
| H3 control — *different user*, asset `UNKNOWN` | **Pass (privacy holds).** User C got a **separate content asset** (`c43ac2d5…` ≠ `aa28e10d…`), ran the full pipeline independently, ready in 6.0s. No cross-user leak. |
| H3 — *different user*, asset `PUBLIC` | **Pass.** User B got a **new item** (`b72cf7d9…`) sharing **the same content** (`aa28e10d…`), chunks copied (1 row), status `ready` in **2.0 s** (no fetch/understand/embed). Visibility restored to `UNKNOWN` afterwards. |
| How the two identities were created | The running API container can only ever be the default dev identity (`identity.dev_user` reads one email), so the test switched `DEV_AUTH_EMAIL` between calls in-process against the real route function — the only way to express "second user" under `DEV_AUTH_ENABLED`. |

**Gap found:** no production code path ever assigns `visibility = 'PUBLIC'`
(the only writers are tests). Since `_find_reusable_asset` requires PUBLIC (or
same owner), **cross-user H3 reuse can never trigger in the running app** —
every asset stays `UNKNOWN`, so two users saving the same public link each pay
a full processing run. The H3 code itself works when the condition holds (test
above), but the condition is unreachable in production.

## 5. The stuck save (wikipedia Mushroom_risotto) — root cause

Observed state: item `pending` forever; job `PROCESSING`, `attempt_count = 0`,
`locked_at = NULL`, `claimed_at = NULL`, no `last_stage`, no `last_error`.
Worker log shows exactly two deliveries, both no-ops:

```
05:52:07 Task process_item[…] succeeded in 0.87s: {'status': 'skipped', 'reason': 'claimed by another worker'}
05:52:08 Task process_item[…] succeeded in 0.02s: {'status': 'skipped', 'reason': 'claimed by another worker'}
```

Deduction (each step backed by the data above):

1. The save committed the job `PENDING` at 05:52:05 and the API's fast path
   published a Celery message.
2. The outbox dispatcher's next 5-second tick landed inside the ~1s window
   before the worker's claim, found the row `PENDING`, locked it and published
   a *second* message.
3. Both deliveries ran `claim_job` while the outbox lock was live → both
   returned "claimed by another worker" (`locked_at` was set and recent).
4. The outbox then ran `mark_processing` → status `PROCESSING`,
   `locked_at = NULL` — **with no worker ever owning the job** (`claimed_at`
   still NULL is the proof: only a successful `claim_job` stamps it).
5. Nothing can ever retry it: `claim_batch` only re-publishes `PENDING` jobs,
   Celery messages are exhausted, and re-saving the link goes through the
   dedupe/`_touch` path, which does not enqueue (verified: job row untouched
   after a later re-save of the same URL during the full test-suite run).

Net: a rare claim race (1 of 22 saves) produces a **permanent strand** — the
job looks "in flight" forever and the item looks "pending" forever with no
error anywhere. Phase 18 added `locked_at`/`claimed_at` exactly for detecting
this, but no stale-PROCESSING reaper exists.

## 6. Failure causes grouped by type

| # | Group | Saves affected | Evidence |
|---|-------|----------------|----------|
| F1 | **Page fetch produces zero bytes** (Firecrawl placeholder key → 401; Jina host `cc.jina.ai` does not resolve; empty `raw_preview`) | **22 / 22** | 21× firecrawl 401 in worker logs; direct Jina probe: DNS failure vs `r.jina.ai` 200; `raw_preview` empty on all items |
| F2 | **Gemini free-tier chat quota exhausted** (20 req/day/model) → silent heuristic brief | **17 / 22** ready saves (503-group is F4; 3 saves got AI briefs before quota died; 1 never ran) | `[extractor] AIHTTPError … 429 … limit: 20` on every extract; API error body: retry in 17h33m |
| F3 | **Job claim race** (outbox lock vs worker claim → `PROCESSING` with no owner, no reaper) | **1 / 22** | §5 |
| F4 | **Transient provider 503** (high demand) after retries exhausted | **1 / 22** (iPhone 16: +24s latency, heuristic brief) | `[extractor] AIHTTPError … 503 UNAVAILABLE` |
| F5 | Non-failures: unreachable / private / long-page all ended `ready` (limited) in ~4s, no hangs; embeddings 100% success (34/34 items with `gemini-embedding-001`); dedupe/notes/H3-correct | — | §2, §4 |

## 7. Cost and latency per save

- **Monetary cost this pass: $0.** Everything ran on free tiers: chat quota is
  20 requests/day (exhausted), embedding calls are free and all succeeded,
  Firecrawl never authenticated (401, no billable call), Jina endpoint is free.
  `.env` has no billing-backed provider in use.
- **Token cost per save: not measurable from the app** — no usage/token counts
  are logged or stored anywhere (deferred observation).
- **Latency (save → `ready`, includes queue wait), 21 ready saves:**
  min 4s · median 8s · mean ~8.8s · max 28s (the 503-retry save).
  Fastest class: 4s (quota-blocked saves that skip AI after fast 429s);
  AI-first-try saves: 9–14s; Firecrawl 401 alone adds ~1–3s to *every* save.
- Stuck save: no terminal state (observed >50 minutes).

## 8. Recommended fixes, ordered by how many saves each would rescue

1. **Fix the page-fetch chain — rescues content for 21/22 saves.** Change
   `fetcher.py`'s `cc.jina.ai` → `r.jina.ai` (verified 200 from the
   container), or set a real `FIRECRAWL_API_KEY`; log the swallowed fetch
   exceptions (`except Exception: pass` currently hides the cause entirely);
   consider capturing a preview at save time so the fallback text isn't empty.
   Today **0 of 22 saves** fetched a single byte of the target page.
2. **Resolve the AI chat quota — rescues AI briefs for 17/22 saves.** Paid
   tier or a second provider fallback for `gemini-3.8-flash`, plus a
   *user-visible* degraded flag: right now quota exhaustion is only a worker
   WARNING while items still go `ready` with a `"Link: <url>"` stub, which
   reads like success.
3. **Add a stale-PROCESSING reaper (or fix the outbox claim/publish race) —
   rescues 1/22.** The stranded job can be detected with the Phase 18 columns
   that already exist (`PROCESSING` + `claimed_at IS NULL`, or `locked_at`
   older than the 300s lock timeout): re-queue it instead of leaving it
   permanently invisible to every recovery path.
4. **Tune 503 handling — rescues ≤1/22.** The one 503 save spent ~24s in
   retries and still fell back; arguably working-as-designed, lowest priority.
5. **Make `visibility = PUBLIC` reachable (0 direct saves, unlocks a shipped
   feature).** No production path assigns PUBLIC, so cross-user H3 reuse —
   which works when tested — can never fire; every cross-user duplicate link
   pays a full second processing run.

Dev-tooling notes (0 saves): `alembic/env.py` overrides the migration URL with
`DATABASE_URL`, which makes the documented live-eval pytest command migrate the
live DB (and fail with `relation "users" does not exist`) anywhere
`DATABASE_URL` is set — the eval run needed `env -u DATABASE_URL`; and
`tests/conftest.py` strips AI keys by design, so any provider-backed eval must
bypass pytest.

## 9. Test commands run and results

```
# 1. Phase 19 suite (oracle baseline, in-container workaround documented above)
docker compose exec -e TEST_DATABASE_URL=postgresql://findback:findback@postgres:5432/findback \
  api env -u DATABASE_URL python -m pytest tests/test_phase19_evaluation.py -q -s
  → 31 passed in 5.85s; Recall@1 0.722 / Recall@5 0.944 / MRR 0.819 (matches docs exactly)

# 2. First provider-backed eval run (scratch script calling run_evaluation directly; throwaway DB, dropped)
docker compose exec api python /app/run_real_eval.py
  → provider: provider; Recall@1 0.778 / Recall@5 1.000 / MRR 0.875 (see §3)

# 3. Two-user H3 test (scratch script; visibility restored after)
docker compose exec api python /app/h3_two_user_test.py
  → control not shared (separate content); H3 joined same content, ready 2.0s (see §4)

# 4. Full regression suite (no product code changed in this pass)
docker compose exec -e TEST_DATABASE_URL=postgresql://findback:findback@postgres:5432/findback \
  api env -u DATABASE_URL python -m pytest -q
  → 574 passed, 9 failed in 309.90s (583 total = documented baseline)

# 5. Fetch probes
docker compose exec api python /app/probe_jina.py
  → cc.jina.ai: ConnectError Name or service not known | r.jina.ai: 200 (1081 bytes)
```

The 9 full-suite failures are all environmental/configuration, none caused by
this pass (zero code changes):

- 2 × `test_outbox_wiring` — reads `docker-compose.yml` from the repo root;
  inside the api container only `backend/` is mounted (`/docker-compose.yml`
  missing). Passes from a host checkout.
- 6 × `test_phase16_multitenant::…rejected…` — this environment runs
  `DEV_AUTH_ENABLED=true` by design, so anonymous requests are accepted (200
  instead of 401).
- 1 × `test_phase7_limits::test_retry_is_bounded_and_ends_in_failed` — `.env`
  sets `JOB_MAX_ATTEMPTS=5`, which overrides the test's monkeypatched cap of 3
  (`assert 5 == 3`). A pre-existing test-vs-configuration conflict that fails
  anywhere this `.env` is loaded.

All scratch scripts and output files created during this pass were deleted;
this report is the only artifact.

## 10. Deferred observations

- Issue: starlette `TestClient` is broken in the pinned stack (fastapi 0.110 +
  httpx 0.28: `Client.__init__() got an unexpected keyword argument 'app'`).
  Why it matters: any future in-process API test must call route functions
  directly. Relevant future phase: test tooling.
- Issue: `alembic/env.py` line 9 unconditionally prefers the `DATABASE_URL`
  env var over the configured URL. Why it matters: the documented live-eval
  command migrates the wrong database in any container/CI where
  `DATABASE_URL` is set. Relevant future phase: eval tooling.
- Issue: no AI usage/token telemetry. Why it matters: cost per save cannot be
  measured from the app. Relevant future phase: observability.
- Issue: `.env` `JOB_MAX_ATTEMPTS=5` vs phase-7 test cap 3 (full-suite failure
  above). Relevant future phase: test fixes.
- Issue: fetch exceptions are silent (`except Exception: pass`). Why it
  matters: the entire fetch chain failing for every save produced zero log
  lines; only direct probes found it. Relevant future phase: fetch diagnostics.
- Issue: bad-case coverage caveat (§2) — because Firecrawl 401s and Jina
  DNS-fails *before* contacting the target host, unreachable/private/long-page
  saves all followed the identical path; target-specific handling (real DNS
  failure, real login wall, real >12k-char page) was not exercised in this
  environment. Relevant future phase: fetch-chain re-test after fix #1.
