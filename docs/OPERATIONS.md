# Operations and Data Retention

## Data retention and deletion

What FindBack keeps, for how long, and how it is removed. Phase 14 made this
explicit; before that it was implied by the schema.

## The three lifetimes

They differ on purpose, because the three kinds of data differ.

| Data | Lifetime | Why |
|---|---|---|
| **Raw text** — the fetched page, transcript, raw snapshot | Until the pipeline finishes, then dropped | The stages need it and nothing after them does. It is a verbatim copy of something the user read. |
| **Derived text** — brief, chunks, vectors, search document | As long as the memory exists | This *is* the memory. Removing it would remove the thing the user saved. |
| **User context** — note, intent | Until the user removes it, or the save is deleted | Nobody else's business, and removable on demand. |

`RAW_TEXT_RETENTION_HOURS` controls the first row. It defaults to **0**: raw
text is dropped as soon as the pipeline reaches READY.

## Content that is shared

`content_assets` holds the content itself and may be `PUBLIC`, in which case
more than one user points at it (PRODUCT.md rule 3). Deleting one user's save
deletes that user's items, their `user_memories` row and the derived data that
belongs to them — **and not the asset**, which another user may still hold.

This is the only place in the system where "delete" does not mean "remove every
trace", and it is deliberate.

## Removing things

```python
from app.services import retention

retention.purge_raw_text(db)             # drop raw text, keep the memory
retention.purge_expired_raw_text(db)     # apply the retention window
retention.delete_save(db, user_id, content_id)
retention.retention_policy()             # the policy, as data
```

The active policy is served at `GET /health` under `retention`, so an operator
can confirm it without reading the code.

## What is never written to logs

A saved page, a transcript, and anything derived from them are the user's
content. `app/services/privacy.py` enforces this:

- `describe(value)` logs a length and a SHA-256 digest, never the value.
- `PrivacyFilter` redacts registered private values out of **any** log record,
  including ones produced by httpx, sqlalchemy or uvicorn, and redacts long
  runs of prose that were never registered.
- Values are registered by digest, so the filter is not itself a second copy
  of the private data.

The filter is installed on every handler at startup in `app/main.py`.

## The AI gateway

All model access goes through `app/services/ai_gateway.py`. Provider
configuration — keys, endpoints, model ids — lives in `app/services/ai.py` and
in environment variables; no other module names a provider. Swapping providers
means registering a factory and setting the name, not editing callers.

The provider sends the user's content to a third party. That is inherent to the
product and is the reason this page exists.


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
`android/` and `ios/` are generated per developer machine: run `tool/setup_mobile.sh` from the repository root (it calls `flutter create` in a temp dir and copies only the platform folders in, then runs `pub get`, `analyze` and `test`). The Dart half of share intake is implemented; the native half is not, because no platform project is checked in -- see `docs/NATIVE_SHARE.md` for the intent filter and Share Extension target that have to be added once one exists.

## Quality gates
`python -m compileall backend/app backend/alembic`; `pytest` in `backend` (set `TEST_DATABASE_URL` or the 272 live-PostgreSQL tests skip themselves); `flutter analyze` and `flutter test` in `mobile`; and `python eval/eval_offline.py` from the repository root.
