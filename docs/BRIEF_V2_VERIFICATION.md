# Brief v3.2 verification

Verified 2026-10-06 (Africa/Cairo). Results below are observed, not completion claims for unresolved reliability issues.

## Real runtime and saved record

The Compose PostgreSQL database was empty. It was backed up before existing migrations were applied to `0012_brief_v2`. API/worker use Compose PostgreSQL at `postgres:5432/findback` (DATABASE_URL); the temporary override `/tmp/findback-reel-runtime.yml` publishes it at localhost:55433 because host port 5432 is occupied. The repository Compose configuration was not changed. Restarting without the override can reintroduce the port conflict. No historical backup was restored.

Normal HTTP POST `/api/v1/ingest` of https://www.facebook.com/share/r/19PXq2Y3AR/ created item `bfa5da53-099a-44ea-9ec6-99e9d02bd804`. The historical requested item `e310cfa3-b604-46f2-b4b0-b5984a3f596b` is absent; this fresh record does not establish its former state.

The automatic worker completed in approximately 102 seconds. Stored status is READY, needs_retry=false, brief_source=llm, evidence_level=full_transcript, prompt_version=brief_v3.2. Gemini gemini-3.8-flash produced the saved Brief. There are 18 transcript chunks, all embedded, and a memory embedding, using gemini-embedding-001. No manual provenance/state writes or process-local provider overrides were used.

## Timestamp grounding

Production supplies numbered {id,start,text} transcript segments. Points must cite known integer segment_ids; missing/unknown IDs cause rejection and one repair attempt. Code derives mm:ss by flooring the earliest cited start. IDs survive splitting/map-reduce. The Claude Mem regression uses start 39.28 seconds and yields 00:39; the saved Claude Mem point also cites 00:39. Known but semantically wrong IDs remain possible: this validator is not semantic entailment proof.

## Five real production-path evaluations

The evaluator calls production stage_understand/stage_brief/stage_chunk, using the same prompt, gateway, provider order, backoff, validation and repair. Five serial runs reused the stored evidence. Gemini returned quota 429; automatic failover used configured Groq openai/gpt-oss-120b, including backoff for Groq TPM throttling. Final checks passed 3/5 (60%); runs 1 and 3 returned fallback. The command exited 1. These evaluation outputs did not replace the accepted stored Gemini Brief.

| Run | Result | Every validation rejection (including repaired attempts) |
|---|---|---|
| 1 | FAIL: fallback | 1: schema — Schema validation: title (string_too_long); 2: schema — Schema validation: entities.numbers.0 (string_type) |
| 2 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |
| 3 | FAIL: fallback | 1: schema — Schema validation: entities.numbers.0 (string_type); 2: unsupported_name — Named entity requires written-source confirmation, not an STT guess [skill library, pickabla] |
| 4 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |
| 5 | PASS | 1: unsupported_name — Named entity requires written-source confirmation, not an STT guess [Fine skills, Pickabla] |

No timestamp or tag-count validation rejection occurred in these five runs. Fallback outputs failed final tag-count/timestamp/confidence checks as well as real_brief. “Fine skills” and “Pickabla” are transcript guesses absent from caption/OCR. “skill library” is a generic transcript phrase, not a confirmed product name; its rejected response also included unsupported Pickabla. No demonstrated rejection of a caption/OCR-supported name required loosening the validator. Schema repairs remain unreliable: title length and numeric entity string types caused failures. This phase reports those failures rather than claiming five successful runs.

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

- Worker interruption recovery remains the next task; not changed or claimed verified.
- Five-run real-LLM reliability is 60%; schema repair failures and recurring STT name guesses remain.
- Temporary Compose port override is required on this host while port 5432 remains occupied.
- The original historical record and its former v2 JSON are absent, so no before/after database comparison is claimed.

STOPPED.
