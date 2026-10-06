# Brief v3.2 verification

Verified 2026-10-06 (Africa/Cairo). Results below are observed, not completion claims for unresolved reliability issues.

## Real runtime and saved record

The Compose PostgreSQL database was empty. It was backed up before existing migrations were applied to `0012_brief_v2`. API/worker use Compose PostgreSQL at `postgres:5432/findback` (DATABASE_URL); the temporary override `/tmp/findback-reel-runtime.yml` publishes it at localhost:55433 because host port 5432 is occupied. The repository Compose configuration was not changed. Restarting without the override can reintroduce the port conflict. No historical backup was restored.

Normal HTTP POST `/api/v1/ingest` of https://www.facebook.com/share/r/19PXq2Y3AR/ created item `bfa5da53-099a-44ea-9ec6-99e9d02bd804`. The historical requested item `e310cfa3-b604-46f2-b4b0-b5984a3f596b` is absent; this fresh record does not establish its former state.

The automatic worker completed in approximately 102 seconds. Stored status is READY, needs_retry=false, brief_source=llm, evidence_level=full_transcript, prompt_version=brief_v3.2. Gemini gemini-3.8-flash produced the saved Brief. There are 18 transcript chunks, all embedded, and a memory embedding, using gemini-embedding-001. No manual provenance/state writes or process-local provider overrides were used.

## Timestamp grounding

Production supplies numbered {id,start,text} transcript segments. Points must cite known integer segment_ids; missing/unknown IDs cause rejection and one repair attempt. Code derives mm:ss by flooring the earliest cited start. IDs survive splitting/map-reduce. The Claude Mem regression uses start 39.28 seconds and yields 00:39; the saved Claude Mem point also cites 00:39. Known but semantically wrong IDs remain possible: this validator is not semantic entailment proof.

## Earlier baseline: five real production-path evaluations

The evaluator calls production stage_understand/stage_brief/stage_chunk, using the same prompt, gateway, provider order, backoff, validation and repair. Five serial runs reused the stored evidence. Gemini returned quota 429; automatic failover used configured Groq openai/gpt-oss-120b, including backoff for Groq TPM throttling. Final checks passed 3/5 (60%); runs 1 and 3 returned fallback. The command exited 1. These evaluation outputs did not replace the accepted stored Gemini Brief.

| Run | Result | Every validation rejection (including repaired attempts) |
|---|---|---|
| 1 | FAIL: fallback | 1: schema — Schema validation: title (string_too_long); 2: schema — Schema validation: entities.numbers.0 (string_type) |
| 2 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |
| 3 | FAIL: fallback | 1: schema — Schema validation: entities.numbers.0 (string_type); 2: unsupported_name — Named entity requires written-source confirmation, not an STT guess [skill library, pickabla] |
| 4 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |
| 5 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |

No timestamp or tag-count validation rejection occurred in these five runs. Fallback outputs failed final tag-count/timestamp/confidence checks as well as real_brief. “Fine skills” and “Pickabla” are transcript guesses absent from caption/OCR. “skill library” is a generic transcript phrase, not a confirmed product name; its rejected response also included unsupported Pickabla. No demonstrated rejection of a caption/OCR-supported name required loosening the validator. In this earlier baseline, title length and numeric entity string types caused schema repair failures. The follow-up results below supersede that reliability measurement.

## Search against distractors

Seeded 16 labelled synthetic distractors covering AI/coding, similar Claude material, a recipe, product and article. Corpus: 17 READY items. Seeds use real memory embeddings, empty brief_v2 and timestamps matching the target. They have no transcript chunks, whereas the target does; this is a small synthetic benchmark, not a production-scale relevance claim.

| Query | Rank | RRF score |
|---|---|---|
| that video about claude code skills for developers | 1 | 0.031327828173350454 |
| the AI clip that remembers projects | 1 | 0.030963529277715188 |
| the reel with planning memory and frontend tips | 1 | 0.03132782784616519 |

All three queries surfaced the reel through lexical_search, vector_search, chunk_lexical_search and _chunk_vector_rows. Tags participate in lexical indexed text; there is no independent tag candidate channel. Search tracing was updated for the user patch’s synchronous chunk-vector path; production ranking was not changed.

## Stored caption and grounding

```text
Unlock the 5 Claude Code skills every developer should know. Most people only scratch the surface of what Claude can do. These five skills help Claude automatically find the right tools, plan before writing code, remember your projects across sessions, generate better frontend designs, and continuously improve the way it works with you. Whether you're building websites, apps, or AI projects, these skills can save hours of work and dramatically improve your workflow.
Comment "Claude" below, and I'll send you all 5 GitHub repos.
[keywords: Claude Code, Claude AI, Claude skills, Claude Code skills, AI coding, AI programming, Anthropic, frontend development, AI memory, Claude Mem, Task Observer, Superpowers, prompt engineering, coding assistant, AI developer tools]
#claudecode #claudeai #coding #ai #programming
```

“GitHub repos” and “Comment Claude” are explicitly supported by the caption. Anthropic appears in its keywords. IMPECCABLE appears in OCR. Claude Mem, Superpowers and Task Observer are supported by caption. The first skill’s exact name is not confirmed; the saved point describes skill discovery rather than naming Fine skills. STT still contains corrupted names; no new small-versus-base timing comparison was run in this phase.

## Raw stored Brief JSON

The following is the unedited database brief_v2::text export of the fresh record, not the absent historical item.

```json
{"tags": ["claude skills", "claude", "claude code", "claude ai", "superpowers", "claude mem", "task observer", "impeccable", "anthropic", "ai coding", "ai programming", "developer productivity", "coding assistant", "ai developer tools", "frontend development", "github repos", "peterandstewiecode", "facebook reel", "programming tutorial"], "title": "5 Claude Code Skills to Improve AI Coding and Project Memory", "topics": ["AI coding", "developer productivity", "frontend design", "project memory", "prompt engineering"], "entities": {"numbers": ["5", "2"], "people_orgs": ["Peterandstewiecode", "Anthropic"], "tools_products": ["Claude", "Claude Code", "Claude AI", "Superpowers", "Claude Mem", "Impeccable", "Task Observer", "GitHub"]}, "confidence": "high", "key_points": [{"point": "Skill discovery: Automatically searches the skill library based on project requirements to locate and install needed tools without manual selection.", "source_ref": "00:14", "segment_ids": [4, 5]}, {"point": "Superpowers: Enforces a structured planning phase where Claude verifies intended actions before modifying code to prevent breaking changes.", "source_ref": "00:24", "segment_ids": [6, 7, 8, 9]}, {"point": "Claude Mem: Maintains persistent memory across multiple sessions to retain project file structures and previous history without repetitive re-prompting.", "source_ref": "00:39", "segment_ids": [9, 10, 11]}, {"point": "Impeccable: Enhances frontend user interface generation by applying design references to improve code output quality.", "source_ref": "00:54", "segment_ids": [12, 13]}, {"point": "Task Observer: Runs in the background to monitor developer coding style and continuously tune Claude's performance.", "source_ref": "01:04", "segment_ids": [14, 15, 16]}], "brief_source": "llm", "content_type": "list", "missing_info": null, "best_takeaway": "Combining planning and cross-session memory skills prevents Claude from introducing regressions and eliminates repetitive context setup across coding sessions.", "evidence_used": ["transcript", "caption", "ocr", "metadata"], "instant_brief": "Five specialized skills enhance Claude Code for development tasks by automating tool selection, planning, persistent memory, UI styling, and workflow adaptation. These skills enable Claude to retain project context across sessions and review actions before altering codebases. Repositories are shared via direct message or channel links in the profile.", "likely_intent": "You may have saved this to look up specific Claude skills for project memory, code safety, and frontend generation.", "evidence_level": "full_transcript", "prompt_version": "brief_v3.2", "search_phrases": ["5 claude code skills every developer should know", "claude mem persistent memory across sessions", "superpowers skill for claude to stop breaking code", "how to make claude remember project files", "task observer claude skill learns your style", "impeccable claude frontend design skill", "peterandstewiecode claude skills video"], "suggested_action": "Check the author's bio link or comment on the original post to obtain the five skill repositories."}
```

First 600 characters of stored transcript segment text joined in order with spaces:

```text
PETA, everyone's Claude is insanely fast, and mine is dumb, and can't even remember anything. I'll make your Claude an absolute beast, with just five skills. Okay wait, Claude has so many skills, you're telling me I only need five? You don't need all of them, you just need the right five. All right, what's the first one? Fine skills. What's that? You tell Claude what you're building, and it searches through the skill library finds what you need and installs it for you. So I don't even have to know which skill I'm looking for. Nope. Okay, what else? Superpowers. What's that? Think of it like a 
```

## Prompt changes: brief_v2 to brief_v3.2

- Numbered transcript segments and segment_ids citations; application-owned source_ref, no model timestamps.
- Written-source confirmation of named tools/organizations; avoid STT guesses and unsupported names/tags.
- Synthesized key points rather than copied transcript lines; evidence-based confidence and missing_info.
- Supported phrase tags such as claude skills; avoid boilerplate and unsupported distribution/built-in claims.
- Caption-only instructions belong in instant_brief/suggested_action, not transcript-cited key points.

## Commands and actual tests

```bash
docker compose -f docker-compose.yml -f /tmp/findback-reel-runtime.yml up -d --no-deps --force-recreate postgres
docker compose exec -T worker python -u - --stored-item bfa5da53-099a-44ea-9ec6-99e9d02bd804 --runs 5 --output /tmp/facebook-five-live.json < scripts/eval_brief.py
docker compose exec -T api python -u - --item bfa5da53-099a-44ea-9ec6-99e9d02bd804 --output /tmp/facebook-search-live.json < scripts/eval_search_brief.py
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/fb_reel_check backend/.venv/bin/python -m pytest backend/tests/test_audit_fixes.py backend/tests/test_brief_segment_ids.py backend/tests/test_brief_v2.py backend/tests/test_brief_integrity.py backend/tests/test_brief_worker.py backend/tests/test_phase12_hybrid.py -q
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/fb_reel_check backend/.venv/bin/python -m pytest backend/tests -q
```

Focused: 82 passed in 42.07s. Full: 747 passed, 1 skipped, 1 warning in 287.20s. Warning: python-jose datetime.utcnow deprecation. Tests use isolated fb_reel_check, not the application database. Direct stored-record assertions passed for native provenance, grounding validation, embedding presence and Claude Mem 00:39. Mobile tests NOT RUN: no mobile changes.

Artifacts and backups: `/home/moaz/.codex/backups/findback/reel-check-20261006/`: before-bootstrap.dump, accepted-reel-and-distractors.dump, stored-proof.json, stored-brief-raw.json, five-live.json, search-live.json. The five-live artifact contains complete unedited run outputs and rejection metadata.

## Deferred observations

- Worker interruption recovery was deferred in the earlier baseline; it is now implemented and verified in the follow-up below.
- Earlier five-run real-LLM reliability was 60%; the follow-up below measures the applied repairs.
- Host PostgreSQL occupies port 5432; the later outbox network fix below makes Compose port 55433 permanent.
- The original historical record and its former v2 JSON are absent, so no before/after database comparison is claimed.


## Follow-up: grounding and worker ownership fixes

Verified 2026-10-06 (Africa/Cairo), after the user applied the grounding/recovery patch. No new API response fields or endpoints. Prompt text/version is unchanged (`brief_v3.2`).

### Changes and regression evidence

- Casing no longer exempts guessed names from prose grounding. Unsupported lowercase `pickabla` and `fine skills` are rejected if used in prose. The exact generic phrase `skill library` remains allowed as a description only when it appears in source evidence; it is pruned from unconfirmed entities. Tags containing dropped names are also removed.
- Each successful claim creates a new UUID `processing_jobs.attempt_token`. Heartbeats update only their own token. Recovery revokes the lost token. Worker-session flush/commit checks take a job row lock and reject superseded writes, including writes committed within pipeline stages. A superseded attempt rolls back and exits without marking its replacement failed.
- A dispatcher publish completion cannot clear an already-claimed worker lock. The task path that creates a missing job now claims it before starting a heartbeat.
- New regressions failed against the old code (lowercase prose accepted, ownership column/function absent, publisher cleared a claimed lock). The composite guessed-name tag regression also failed before its fix. Normal retry integration initially exposed retained-token blocking; it was corrected and the final regression checks passed.
- Migration `0013_job_attempt_token` adds one nullable UUID column, without rewriting existing data. Downgrade/re-upgrade tests preserve existing jobs and initialize their token to NULL. Workers/API/outbox were stopped before migration and rebuilt/restarted afterward. The original saved reel Brief was compared with its earlier export and is unchanged.

### Real LLM rerun: 5/5 passed

Command (completed, exit 0):

```bash
docker compose exec -T api python -u - --stored-item bfa5da53-099a-44ea-9ec6-99e9d02bd804 --runs 5 --output /tmp/facebook-five-ownership.json < scripts/eval_brief.py
```

All five final evaluations passed every evaluator check, using real Groq `openai/gpt-oss-120b` responses after normal Gemini failover. First-response acceptance was 0/5: each run rejected unsupported STT guesses once and passed after the single automatic repair. There were no schema, timestamp or tag-count validation rejections in this rerun. No fallback output remained. These evaluations did not overwrite the accepted original Gemini Brief.

| Run | Final result | Rejection before successful repair |
|---|---|---|
| 1 | PASS | attempt 1: unsupported_name (Fine skills, Pickabla) |
| 2 | PASS | attempt 1: unsupported_name (Fine Skills, Pickabla) |
| 3 | PASS | attempt 1: unsupported_name (Fine skills, Pickabla) |
| 4 | PASS | attempt 1: unsupported_name (Fine skills, Pickable) |
| 5 | PASS | attempt 1: unsupported_name (Fine skills, Pickabla) |

Provider logs still show Gemini HTTP 429 quota exhaustion (`generate_content_free_tier_requests`, limit 20) and HTTP 503 high demand. Groq also returned token-per-minute 429s; the production retry/backoff path recovered. This proves five repaired successes through secondary-provider failover, not successful Gemini chat quota recovery or a statistically established future pass rate.

### Live SIGKILL recovery

Created a separate verification user through normal signed-token authentication, then saved the same Facebook reel through HTTP POST `/api/v1/ingest`. Test item: `e8309d0f-a170-43b8-82e6-3f353675c556`. No other claimed work was active before the kill. No saved-record state/provenance was manually edited and no clock/lock timeout was shortened.

The actual Compose worker container was SIGKILLed after its native job claim and committed PROCESSING state, then restarted. The unchanged dispatcher recovered it after 302.01 seconds observed from the kill, counted one failed attempt, and republished after normal backoff. Observed states: PROCESSING/0 → PENDING/1 → PROCESSING/1 → READY/2. The final count of 2 comprises the worker-loss failure and the successful replacement attempt.

- Old token: `ba948ad5-5d55-4885-8515-ec0b17d4487c`.
- Replacement token: `67883cc1-1c4e-47a2-a413-8257513328dc`.
- Final item/job: READY / READY; needs_retry=false.
- Native stored provenance: brief_source=llm, evidence_level=full_transcript, prompt_version=brief_v3.2.
- Total kill-to-observed-completion: 422.09 seconds. The restarted pipeline included media acquisition/STT and real-provider failover; no fixture LLM was used.

The original item `bfa5da53-099a-44ea-9ec6-99e9d02bd804` remains READY with its prior real Gemini Brief and needs_retry=false. The historical `e310cfa3-b604-46f2-b4b0-b5984a3f596b` remains absent.

### Actual test commands and results

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/fb_reel_check backend/.venv/bin/python -m pytest backend/tests/test_brief_grounding_repair.py backend/tests/test_worker_loss_recovery.py backend/tests/test_brief_integrity.py backend/tests/test_stuck_save_recovery.py backend/tests/test_phase5_outbox.py backend/tests/test_phase16_migration.py -q
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/fb_reel_check backend/.venv/bin/python -m pytest backend/tests -q
PYTHONPATH=backend backend/.venv/bin/python -u /tmp/findback-live-worker-kill.py
docker compose -f docker-compose.yml -f /tmp/findback-reel-runtime.yml up -d --build --no-deps api worker outbox
```

Final focused: **58 passed in 33.10s**. Final full suite: **765 passed, 1 skipped, 1 warning in 358.45s**. An earlier full run reported 2 failed, 761 passed, 1 skipped: the two migration tests still expected head 0012 and were updated to the actual new head 0013; final suite includes the two subsequently added tag/migration regressions. The warning is python-jose datetime.utcnow deprecation. Test databases are isolated; mobile tests NOT RUN (no mobile changes). Python compilation and CRLF-aware git diff --check passed.

Real database migration: `0012_brief_v2` → `0013_job_attempt_token`, after backup `ownership-fix-20261006/before-0013.dump`. No backup restore was performed. API/worker/outbox are rebuilt and running against Compose PostgreSQL; host port override remains localhost:55433.

Artifacts: `/home/moaz/.codex/backups/findback/ownership-fix-20261006/`: before-0013.dump, original-reel-after-migration.json, five-live.json, five-live.log, worker-kill.json, recovered-reel-proof.json, worker-kill-check.py, focused-tests.log, full-tests.log. The JSON artifacts contain complete results/provenance. The kill harness was a temporary verification script, not a new application feature.

### Remaining limitations

- Fallback Brief retries remain unbounded with capped backoff; this behavior was not changed.
- The five-minute recovery delay remains intentional and was measured, not shortened.
- Attempt fencing prevents superseded database commits. It does not promise exactly-once outbound provider calls when workers overlap.
- Known segment IDs can still be semantically inappropriate; timestamp derivation alone is not entailment verification.
- Gemini chat quota is still exhausted; successful reruns relied on Groq failover.


## Outbox network repair — 2026-10-06

A later ordinary Compose restart recreated PostgreSQL with no Docker network attachment. Outbox repeatedly reported `could not translate host name "postgres" to address: Name or service not known`. Host PostgreSQL still occupied port 5432.

Changed the PostgreSQL host mapping in docker-compose.yml to `127.0.0.1:55433:5432`, making the earlier temporary workaround permanent. Internal API/worker/outbox DATABASE_URL values remain `postgres:5432/findback`. Recreated only PostgreSQL with `docker compose up -d --no-deps --force-recreate postgres`. No schema migration, backup restore, application code change or data deletion was performed. The existing `findback_pgdata` volume and migration `0013_job_attempt_token` were retained. Backup: `/home/moaz/.codex/backups/findback/outbox-network-20261006/before-network-fix.dump`.

Verified from the outbox container: PostgreSQL DNS resolves, SQL `select 1` returns 1, and production `dispatch_once` completes with zero failures. API /health reports db=ok and redis=ok. The before-fix dump and current database each contain four saved items; this is the later user dataset, not the earlier synthetic search corpus.

Regression command actually run:

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/fb_reel_check backend/.venv/bin/python -m pytest backend/tests/test_outbox_wiring.py backend/tests/test_phase5_outbox.py backend/tests/test_worker_loss_recovery.py -q
```

Result: **18 passed in 12.09s**. The new Compose port/internal-address regression failed against the old mapping, then passed after the change. Full suite NOT RUN in this configuration-only follow-up; the related tests and live DNS/DB/dispatcher checks were run.

STOPPED.
