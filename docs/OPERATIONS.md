# Operations

## Local run
Copy `.env.example` to `.env`, set `DEV_AUTH_ENABLED=true` only for local development, then run `docker compose up --build`. Apply schema changes with `docker compose exec api alembic upgrade head`.

## External integrations
AI providers, Firecrawl, S3/R2, and Supabase JWT verification are environment-gated. Without AI/scraping credentials, ingestion uses preview text and deterministic extraction; without S3 credentials, raw snapshots remain unstored. Production must set `DEV_AUTH_ENABLED=false`, `SUPABASE_JWT_SECRET`, and restrictive `CORS_ORIGINS`.

## AI providers

Extraction and embeddings resolve independently from `.env` (see `.env.example`). With `AI_PROVIDER` unset the chat provider is auto-detected from the first key present, in the order Groq, Gemini, OpenAI. Embeddings never fall back to Groq: its `nomic-embed-text` model is ~137-dim and rejects empty input, so a Groq-only setup runs with keyword search instead of vectors. The intended free-tier pairing is Groq for chat plus Gemini for embeddings.

Run the connectivity check from `backend/`:

```bash
python scripts/check_ai.py                # resolved config and a live chat ping
python scripts/check_ai.py --list-models   # model ids the provider currently offers
python scripts/check_ai.py --embeddings    # embed a probe and report the vector width
```

Model ids are retired by providers; when one is rejected the error names it, and the fix is to set `EXTRACTOR_MODEL` or `EMBEDDING_MODEL` in `.env` — no code change. Every AI request is attempted `AI_MAX_RETRIES` times; timeouts, `429`, and `5xx` are retried, other `4xx` are not. A request field the provider refuses is dropped and retried once.

### Changing the embedding width

`EMBEDDING_DIMS` must match `Vector(1536)` in `app/models.py` and `alembic/versions/0001_initial.py`, and the width requested from the provider. To change it: migrate the column to the new width, set `EMBEDDING_DIMS` and a matching `EMBEDDING_MODEL`, then re-embed every row (`items.embedding` and `chunks.embedding`) and update `embedding_model`. A width mismatch is refused before insert (`_guard_dimensions` in `app/services/ai.py`), so a mis-set value degrades to keyword search rather than corrupting the column.

## Mobile native build
Run `npx expo prebuild` after setting `IOS_APP_GROUP`. The config plugin adds Android `ACTION_SEND`; iOS signing, App Group entitlement, and Share Extension target require the Apple team/profile in EAS or Xcode. Background sync is best-effort and OS scheduled.

## Quality gates
`python -m compileall backend/app backend/alembic`, `pytest` in `backend`, `npm ci && npx tsc --noEmit` in `mobile`, `python eval/eval.py --offline`, and Postman artifact linting.
