# Brief pipeline upgrade verification

Implemented the complete supplied task in eight reviewable commits. No bulk
reprocessing of existing saves was requested or performed.

## Files and resulting behavior

| Files | Change |
|---|---|
| `backend/app/models.py`, `schemas.py`, `alembic/versions/0012_brief_v2.py` | Add versioned Brief/evidence/processing JSONB and retry flag; expose additive API fields while preserving legacy summary and string points. |
| `backend/app/prompts/brief_v2.txt`, `services/brief_v2.py`, `services/extractor.py` | Exact supplied prompt, strict schema and tag validation, one repair attempt, timestamp grounding, bounded map/pairwise merge and grounded fallback. Remove caption suppression of useful facts. |
| `backend/app/services/fetcher.py`, `media_understanding.py` | Preserve YouTube caption timestamps, acquire bounded yt-dlp media, mono audio, scene frames, bilingual OCR, temporary cleanup and privacy-scoped evidence cache. |
| `backend/app/services/transcription.py` | Replaceable multilingual local/cloud STT, configurable routing, timestamped segments and bounded cloud uploads. |
| `backend/app/services/pipeline.py`, `brief_retry.py`, `outbox.py`, `backend/app/tasks.py` | Store evidence/usage metadata; retain a searchable Brief while durable bounded evidence retries run; atomically schedule retries; upgrade and re-embed stronger evidence. |
| `backend/app/services/ai.py`, `ai_gateway.py`, `backend/app/env.py` | Preserve existing provider abstraction, normal/complex model routing, usage/cost metadata and secret masking. |
| `backend/app/services/search.py`, `embedder.py` | Index/embed Brief facts, tags, phrases and entities; preserve Arabic terms and cheaply expand minor tag typos. Exclude missing-information text. |
| `mobile/lib/models/item.dart`, `data/local_db.dart`, `features/home/detail_page.dart` | Additive parsing and SQLite cache migration; Instant/Full Brief, timestamp links, source opening, polling and subtle improvement state. |
| `backend/Dockerfile`, `requirements.txt`, `.dockerignore`, `.env.example`, `README.md`, `docs/BRIEF_V2.md` | Runtime dependencies, bounded settings, deployment and evaluation instructions. |
| `backend/tests/test_brief_{v2,retry,worker,search}.py`, `test_media_{understanding,frames}.py`, `test_transcription.py` | Mocked schema, media, STT, OCR, cache, retry, crash durability, upgrade and search proof. |
| `backend/tests/{conftest,test_video_input,test_h3_content_reuse,test_schema_parity,test_phase1_migration,test_phase16_migration,test_stuck_save_recovery}.py` | Keep existing regressions aligned with the additive schema and intended caption behavior; disable network acquisition in CI. |
| `mobile/test/brief_{v2,detail,cache_migration}_test.dart` | Model compatibility, timestamp URLs, processing transitions and preservation of SQLite v1 saves. |
| `backend/tests/fixtures/brief_v2.json`, `scripts/eval_brief.py` | Six-case mocked evaluation and optional live/Facebook comparison. Caption baseline explicitly excludes transcripts. |

## Migration and local runtime

Applied `0011_job_claim_time -> 0012_brief_v2` to the local `findback` database with:

```bash
cd backend
DATABASE_URL=postgresql://findback:findback@localhost:5432/findback .venv/bin/python -m alembic upgrade head
```

Real PostgreSQL tests exercise downgrade/reapply and legacy-data preservation.
SQLite v1-to-v2 migration is also tested. Existing saves remain readable; new jobs
create v2 artifacts. There is no automatic backfill. New environment variables and
all defaults are listed in [BRIEF_V2.md](BRIEF_V2.md#environment-settings) and
`.env.example`; credentials are not committed.

Docker image verification installed ffmpeg, Tesseract English/Arabic, yt-dlp and
faster-whisper. The first Compose build encountered a host Docker credential-helper
error. A retry with an empty temporary Docker client configuration successfully
built `api`, `worker` and `outbox`, without changing the user's Docker configuration.

## Facebook before/after: real acquisition

Tested `https://www.facebook.com/share/r/19PXq2Y3AR/` using the existing configured
AI provider and the actual media runtime in Docker:

```bash
docker run --rm --env-file .env \
  --mount type=bind,source=/home/moaz/FindBack,target=/workspace,readonly \
  --mount type=bind,source=/tmp/findback-brief-results,target=/results \
  --mount type=bind,source=/tmp/findback-whisper-cache,target=/root/.cache/huggingface \
  --workdir /workspace findback-brief-verification \
  python scripts/eval_brief.py --url 'https://www.facebook.com/share/r/19PXq2Y3AR/' \
  --title '5 claude code skills every developer should know' --output /results/facebook-full.json
```

| Comparison | Observed result |
|---|---|
| Caption only | `partial`, medium confidence, 30 normalized tags, separate missing-info field; provider failure used the grounded offline fallback. Scraped Facebook text contains page controls, so this fallback is noisy. |
| Full pipeline | `full_transcript`, 85.622-second clip, multilingual local `base` STT, approximately 73 seconds transcription / 118 seconds acquisition, no recorded acquisition errors; 30 tags and timestamped transcript points. Provider failure again used offline fallback with medium confidence. |

Full output now includes timestamped spoken evidence, for example:

- `00:17–00:21`: searches the skill library and installs the relevant skill.
- `00:26–00:34`: Superpowers; plan and check before modifying a project.
- `00:45–00:47`: memory across sessions for projects, files and prior work.
- `00:56–00:58`: front-end improvements using design references.
- `01:06–01:11`: Task Observer; observes work style and improves other skills.

These are descriptions of observed transcript passages, not a claim that the
model produced a polished five-item Brief. STT misheard some names (including
“Fine skills”, “Claude, man” and “I'm pickabla”). No unverified corrections were
invented. The live configured Gemini service returned 503/429 responses, so
polished model extraction and exact five-name identification remain unverified.
Both live comparison rows passed the four evaluation checks, **2/2**. These checks
are structural/content-policy checks, not a semantic quality score. Operator JSON:
`/tmp/findback-brief-results/facebook-full.json`. An earlier caption-only successful
LLM run produced five descriptive capabilities, but those were not verified names
of the five skills. The new full run adds actual speech and references.

## Checks and limits

Initial full-suite failures exposed outdated migration fixtures and an old
expectation that failed fetches must become error-message summaries. Those causes
were corrected; failing tests were not hidden. A separate reviewer identified a
READY/retry durability gap and unbounded final merge; both have regression tests.
Reviewer recheck measured 100 segments producing requests up to 10,960 characters
for maps and 1,250 for merges; oversized individual segments also stayed bounded.

Known limits: login-gated media and extractor changes can prevent acquisition;
OCR sampling can miss frames; first local STT run downloads model weights; cloud
long-video STT needs configured credentials. Provider outages retain a grounded
fallback, which can have fewer than the requested tag/phrase counts for sparse
sources and can preserve noisy scraped text or recognition errors. Metadata marks
that fallback. Strict accepted LLM responses still enforce 15–30 tags and 5–8
phrases. Input-budget overflow uses fallback rather than an unbounded model call.

Recommended next steps: restore the configured LLM's capacity/quota and rerun the
live comparison below to assess the polished Brief and actual skill names. Provision
Whisper weights on the worker beforehand if model-download access is restricted.
Use cookies only for authorized content if a platform requires them.

```bash
backend/.venv/bin/python scripts/eval_brief.py
backend/.venv/bin/python scripts/eval_brief.py --live
backend/.venv/bin/python scripts/eval_brief.py --url 'https://www.facebook.com/share/r/19PXq2Y3AR/' --output /tmp/facebook-brief.json
```

Unrelated pre-existing `.agents/skills` edits and Zone.Identifier files were left
untouched and excluded from commits.

## Final verification results

Exact final commands and observed summaries:

```bash
TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests -q
# 668 passed, 1 skipped, 1 warning in 256.62s (0:04:16)

cd mobile && flutter test
# +78: All tests passed!

flutter analyze
# No issues found! (ran in 12.5s)

backend/.venv/bin/python scripts/eval_brief.py --output /tmp/findback-fixture-final.json
# Evaluation: 6/6 cases passed

TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/fb_brief_upgrade backend/.venv/bin/python -m pytest backend/tests/test_brief_v2.py backend/tests/test_brief_worker.py backend/tests/test_media_understanding.py -q
# 25 passed in 5.63s
```

The skipped test and python-jose datetime deprecation warning are existing suite
conditions. Flutter results cover the complete test directory. No phone/device
installation or release deployment was performed.

Local runtime refreshed with `docker compose up -d --no-deps api worker outbox`.
All five existing services report `running`; `curl -fsS http://localhost:8000/health`
returned `status=ok`, `db=ok`, `redis=ok`. A worker-container import/tool check
confirmed yt-dlp, faster-whisper, ffmpeg 7.1.5 and Tesseract `ara`/`eng` packs.
This is a local development refresh, not an external production release.
