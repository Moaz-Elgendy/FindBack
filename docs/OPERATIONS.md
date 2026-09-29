# Operations

## Local run
Copy `.env.example` to `.env`, set `DEV_AUTH_ENABLED=true` only for local development, then run `docker compose up --build`. Apply schema changes with `docker compose exec api alembic upgrade head`.

## External integrations
OpenAI, Firecrawl, S3/R2, and Supabase JWT verification are environment-gated. Without AI/scraping credentials, ingestion uses preview text and deterministic extraction; without S3 credentials, raw snapshots remain unstored. Production must set `DEV_AUTH_ENABLED=false`, `SUPABASE_JWT_SECRET`, and restrictive `CORS_ORIGINS`.

## Mobile native build
Run `npx expo prebuild` after setting `IOS_APP_GROUP`. The config plugin adds Android `ACTION_SEND`; iOS signing, App Group entitlement, and Share Extension target require the Apple team/profile in EAS or Xcode. Background sync is best-effort and OS scheduled.

## Quality gates
`python -m compileall backend/app backend/alembic`, `pytest` in `backend`, `npm ci && npx tsc --noEmit` in `mobile`, `python eval/eval.py --offline`, and Postman artifact linting.
