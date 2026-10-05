# Brief pipeline gap verification

Verified 2026-10-05 against the running Docker API, worker and PostgreSQL. This document supersedes the earlier evaluation-only and operator-annotated provenance claims.

## Database and record

The running API/worker use `DATABASE_URL=postgresql://findback:<redacted>@postgres:5432/findback`, configured in `docker-compose.yml`. The active server is Docker PostgreSQL 16, database `findback`, server `172.18.0.3:5432`, directory `/var/lib/postgresql/data`, volume `findback_pgdata` (Docker daemon path `/var/lib/docker/volumes/findback_pgdata/_data`). It is separate from host PostgreSQL 18 at `localhost:5432/findback`, directory `/var/lib/postgresql/18/main`. The active database is at `0012_brief_v2`; no migration was added or applied during these gap fixes.

Target reel: `https://www.facebook.com/share/r/19PXq2Y3AR/`.
Item: `e310cfa3-b604-46f2-b4b0-b5984a3f596b`.
Content: `7a6d30c1-6720-4093-8316-e1c7e1045f5e`.

Before removing the earlier operator annotations, a custom-format backup was written to `/home/moaz/.codex/backups/findback/gap-fixes-20261005T153844Z/before-native-reprocess.dump`. SHA-256: `109253666fd1100ec92ce3b128eb8fe8fe03a2777b6988743450e7ac9840fff7`. Parent/file permissions: 0700/0600. A full restore was NOT RUN.

The only operator record edit removed `brief_source`, `evidence_level`, `prompt_version` from this Brief and `brief_source` from its processing metadata. Item/job states, retry flags and transcripts were not manually changed. The existing outbox automatically recovered the missing/stale provenance, created durable jobs and published normal Celery tasks. All provenance is now assigned by `stage_understand`, including fallback provenance. Prompt version is separate from the existing `process_item:v1` compatibility label.

## Verified failure behavior and configuration

Regression tests cover a complete transcript followed by LLM failure: the stored placeholder has source `fallback`, low confidence, truthful retry note, empty tags/points, `needs_retry=true`, and a durable pending job. It has no final embedding, chunk vectors or searchable document. Every search candidate path excludes fallback records. A subsequent successful LLM Brief replaces it, clears the flag and is embedded normally.

The existing HTTP adapter automatically exhausts configured primary attempts on 429/503/timeout, then uses the configured secondary. `Retry-After` numeric and HTTP-date forms are honored. `AI_MAX_RETRIES` caps attempts per provider; `AI_RETRY_WAIT_BUDGET_SECONDS` caps cumulative sleep, so a longer Retry-After exhausts that provider without retrying early. Schema validation can request a separate repair generation. Permanent HTTP errors do not trigger this failover.

Actual worker configuration: primary Gemini `gemini-3.8-flash`; `SECONDARY_AI_PROVIDER=groq`, `SECONDARY_AI_MODEL=openai/gpt-oss-120b`; `BRIEF_MAX_TOKENS=6000`. No process-local provider/retry overrides were used for these automatic jobs. Mocked 503, 429, timeout, Retry-After and budget cases passed.

Source cleaning fixtures cover the reel's reaction-count title, login controls, See more, Original audio, markdown links and image references while preserving real caption content and the comment instruction. Cleaned caption/keyword terms and observed OCR terms reach Whisper through its initial prompt; old local STT caches without that hint are skipped automatically. The normal base run persisted 18 segments with `stt_prompt_used=true`.

## STT comparison on the actual reel

Same 85.622-second downloaded reel; CPU int8, beam 5, VAD; both models received the same cleaned-source prompt. Times below exclude model loading/download:

| Model | STT seconds | Segments | Recognition observations |
|---|---:|---:|---|
| base | 15.272 | 18 | Fine skills; Superpowers; Claude Mem; I'm pickabla; Task observer |
| small | 51.432 | 40 | Find skills; Superpowers; Claude ma'am; Un picable; Task observer |

Base loaded in 1.696 seconds. Small loaded/downloaded in 147.862 seconds; that is a one-time cost, not its STT time. Small was 3.37 times slower and did not resolve all names. Base remains configured. The normal worker's measured STT duration includes loading and host load and is not substituted for this paired timing.

Caption independently confirms **Superpowers, Claude Mem, Task Observer**; OCR confirms **IMPECCABLE**. The exact first name, including “Find skills,” is not confirmed by caption/OCR and is not certified here. Neither transcription is exact. The prompt and validator reject STT-only named entities, prefer written confirmation and use functional descriptions for unconfirmed names. Unsupported “built-in” availability is also rejected; separate distribution instructions must not be merged into an inferred download destination.

## Final native persistence and grounding

The automatic dispatcher published Celery task `3822be23-82ce-4522-9661-20f4c16b0ac1` for existing job `4e3dbbe7-ff0d-4cc3-9798-2c61a7d895b5`. It resumed the complete-transcript retry through UNDERSTAND, BRIEF, CHUNK and EMBED. Gemini exhausted three 429 attempts; Groq returned real 200 responses, a named-entity repair ran, and Groq 429 backoff was observed before the accepted response. The worker completed at 2026-10-05 16:11:19 UTC.

Direct application-engine queries and assertions established:

```json
{
  "migration": "0012_brief_v2",
  "item_id": "e310cfa3-b604-46f2-b4b0-b5984a3f596b",
  "status": "ready",
  "needs_retry": false,
  "brief_source": "llm",
  "evidence_level": "full_transcript",
  "prompt_version": "brief_v3.2",
  "chunks": 18
}
```

Job is READY, last stage EMBED. Stored real Groq usage: 2 successful generations, 6845 input tokens, 3436 output tokens, usage reported. The final memory embedding has 1536 dimensions; all 18 transcript chunks have embeddings. This followed the live fallback state observed with `needs_retry=true`, PENDING/NORMALIZE, zero chunks and a null memory embedding. The upgrade was written by the normal worker, not by copying the evaluation artifact or manually annotating its provenance. ItemDetail serializes `brief_source=llm`.

Stored caption, read from the active DB:

```text
Unlock the 5 Claude Code skills every developer should know. Most people only scratch the surface of what Claude can do. These five skills help Claude automatically find the right tools, plan before writing code, remember your projects across sessions, generate better frontend designs, and continuously improve the way it works with you. Whether you're building websites, apps, or AI projects, these skills can save hours of work and dramatically improve your workflow.
Comment "Claude" below, and I'll send you all 5 GitHub repos.
[keywords: Claude Code, Claude AI, Claude skills, Claude Code skills, AI coding, AI programming, Anthropic, frontend development, AI memory, Claude Mem, Task Observer, Superpowers, prompt engineering, coding assistant, AI developer tools]
#claudecode #claudeai #coding #ai #programming
```

Grounding facts: “GitHub repos” and the instruction to comment “Claude” occur in that caption. “Anthropic” occurs in its keywords, supporting the `anthropic` tag as source-derived metadata, not an independent assertion that speech named Anthropic. The stored suggested_action matches the caption instruction. Native tag policy includes `claude skills` because that exact phrase occurs in written evidence; `agent skills` is absent because it is not supported here. The garbled first/frontend STT names are absent from the final named entities; the first skill remains a functional description.

Raw stored Brief JSON, unedited output of `SELECT brief_v2::text FROM items WHERE id=...`:

```json
{"tags": ["claude skills", "claude", "claude code", "superpowers", "claude mem", "task observer", "telegram", "peterandstewiecode", "anthropic", "ai coding", "developer workflow", "code assistant", "frontend design", "planning", "automation", "github repos", "facebook video", "tutorial"], "title": "5 Claude Code Skills Every Developer Should Use", "topics": ["claude code", "ai coding assistant", "developer workflow"], "entities": {"numbers": [], "people_orgs": [], "tools_products": ["claude", "superpowers", "claude mem", "task observer", "telegram", "peterandstewiecode", "anthropic"]}, "confidence": "high", "key_points": [{"point": "Claude can be enhanced with a skill library that searches for needed tools and installs them automatically", "source_ref": "00:14"}, {"point": "Superpowers adds a planning layer that makes Claude pause, plan, and verify before modifying code", "source_ref": "00:19"}, {"point": "Claude Mem gives Claude memory across sessions, remembering project files and prior work", "source_ref": "00:24"}, {"point": "A frontend‑design skill lets Claude improve interfaces using design references", "source_ref": "00:29"}, {"point": "Task observer watches how you work, learns your style, and refines the other skills in the background", "source_ref": "00:34"}, {"point": "The skills are distributed through a Telegram link listed in the creator’s bio", "source_ref": "00:39"}], "brief_source": "llm", "content_type": "video", "missing_info": null, "best_takeaway": "Enabling these five Claude skills can automate tool setup, add safety checks, retain project context, enhance UI design, and personalize Claude’s behavior, saving developers hours of work.", "evidence_used": ["transcript", "caption", "ocr", "metadata"], "instant_brief": "The video outlines five Claude Code skills that automate tool selection, add planning safeguards, provide cross‑session memory, improve frontend design, and learn your work style. Access the skills via the Telegram link in the creator’s bio.", "likely_intent": "You may have saved this to learn which Claude Code skills can boost your development efficiency.", "evidence_level": "full_transcript", "prompt_version": "brief_v3.2", "search_phrases": ["how to get claude code skills", "claude superpowers planning feature", "claude mem cross session memory", "task observer learns my coding style", "telegram link for claude skill repos"], "suggested_action": "Comment \"Claude\" on the post to receive the five GitHub repositories with the skills"}
```

First 600 characters after joining the stored ordered segment text with newline separators (the DB stores timestamped segments, not a standalone transcript string):

```text
PETA, everyone's Claude is insanely fast, and mine is dumb, and can't even remember anything. I'll
make your Claude an absolute beast, with just five skills. Okay wait, Claude has so many skills,
you're telling me I only need five? You don't need all of them, you just need the right five.
All right, what's the first one? Fine skills. What's that? You tell Claude what you're building,
and it searches through the skill library finds what you need and installs it for you. So I don't even have
to know which skill I'm looking for. Nope. Okay, what else? Superpowers. What's that? Think of it like a

```

**Remaining timestamp accuracy limitation:** source_ref values pass the segment-start format/membership validator, but that is not semantic alignment proof. The final Brief gives Superpowers `00:19` although the naming segment starts at 24.24 seconds, Claude Mem `00:24` although the naming segment starts at 39.28 seconds, and the Telegram point `00:39` although its speech instruction starts at 78.48 seconds. The evaluator does not catch these mismatches. Confidence “high” is the model's stored value, not independent certification of every claim/reference. No manual timestamp edits were made.

## Live search against the actual database

Called the existing `search.hybrid_search` with the saved record's owner and normal real query embeddings. That owner's result corpus contained two saved items. No ranking weights were changed.

| Query | Reel rank | Results | Milliseconds |
|---|---:|---:|---:|
| that video about claude code skills for developers | 2 | 2 | 677 |
| the AI clip that remembers projects | 1 | 2 | 1007 |
| the reel with planning memory and frontend tips | 1 | 2 | 526 |

## Real LLM evaluation: separate from persistence

The strengthened evaluator rejects fallback source, boilerplate titles, noisy tags, copied transcript sentences and invalid transcript references. It reads stored evidence from the active application database with `--stored-item`; it does not write an evaluation result back to that database.

Several diagnostic real-provider runs were performed. A v3.1 Gemini run passed 1/1 and a v3.2 Groq run passed 1/1 before the final stricter tag/timestamp checks. Other runs failed validation and returned fallback. The **last** run under the final policy returned fallback after invalid timestamps and STT-only named entities were rejected. Its result is **0/1 cases passed**. This is not a successful current-policy real-LLM Brief, even though Groq returned real HTTP 200 JSON responses and usage. The earlier passes are not substituted for this result.

Command:

```bash
docker compose exec -T worker python -u - --stored-item e310cfa3-b604-46f2-b4b0-b5984a3f596b --output /tmp/brief-v3.2-final.json < scripts/eval_brief.py
```

```json
{
  "name": "stored_evidence_live",
  "brief_source": "fallback",
  "prompt_version": "brief_v3.2",
  "checks": {
    "real_brief": false,
    "clean_title": true,
    "search_tags": true,
    "synthesized_points": true,
    "no_meta_phrases": true,
    "tag_count_15_30": false,
    "timestamped_points": false,
    "confidence_matches_evidence": false
  },
  "llm_usage": {
    "requests": 2,
    "input_tokens": 6787,
    "output_tokens": 3963,
    "usage_reported": true,
    "provider": "groq",
    "model": "openai/gpt-oss-120b"
  }
}
```

```text
Evaluation: 0/1 cases passed
```

## Automated tests

The first full run reported `4 failed, 708 passed, 1 skipped`. Four queue/embedding recovery tests had no successful model response, so the new fallback guard correctly skipped the embedding stage where their failure was injected. They now explicitly mock successful Brief extraction to reach embedding, retaining their original failure/cap assertions. The no-provider test remains separate. Recovery regressions then passed: `17 passed in 16.99s`.

Full command:

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests -q
```

Last full output: `715 passed, 1 skipped, 1 warning in 315.00s`. The warning is the existing python-jose `datetime.utcnow()` deprecation. The last prompt/repair clarification followed that run and was covered by this focused command:

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests/test_brief_integrity.py backend/tests/test_brief_v2.py backend/tests/test_brief_worker.py -q
```

Output: `28 passed in 5.91s`. A broader focused run of Brief, source cleaning, media and AI tests also passed: `146 passed in 14.46s` before the last two small policy regressions were added.

New regression files: `test_ai_resilience.py`, `test_source_cleaning.py`, `test_brief_integrity.py`; integration additions in `test_brief_worker.py`. Existing provider fake responses were adjusted to represent successful LLM responses rather than returning the fallback placeholder as though it came from a model. No test assertions were weakened to hide failures.

CRLF-aware `git diff --check` passed for backend/scripts/config. Flutter/device checks were NOT RUN: no mobile code changed and no device was available. A release/deployment outside the existing local containers was NOT RUN.

## Deferred observation

A worker restart interrupted an already-claimed job. The existing dispatcher only republishes stale unclaimed publications and did not recover that claimed job after five minutes. Its existing task was republished via `process_item.delay` through the normal queue after the lock became stale, with no manual job/item state edits and no `process_item.apply` or process-local provider override. Subsequent fallback retries used the existing automatic dispatcher.

This claimed-job interruption recovery gap is not fixed here. It can strand a claimed job if a worker is killed after receiving an early-acknowledged message. Relevant future phase: worker interruption recovery. This is distinct from the now-tested durable fallback Brief retry path.

Additional focused command (146 passed):

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests/test_brief_integrity.py backend/tests/test_brief_v2.py backend/tests/test_brief_worker.py backend/tests/test_source_cleaning.py backend/tests/test_media_understanding.py backend/tests/test_ai_resilience.py backend/tests/test_ai_transport.py backend/tests/test_ai_parsing.py backend/tests/test_ai_model_selection.py -q
```

Recovery command (17 passed):

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests/test_phase6_state_machine.py backend/tests/test_stuck_save_recovery.py -q
```

All five local Docker services were observed running after verification. The final accepted Brief and search observations are separate from the last failed standalone evaluation, which did not overwrite the saved record.
