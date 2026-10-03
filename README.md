# FindBack — AI-Powered Memory for the Internet

> **"You don't need to remember where you saved it. Just remember what you remember about it."**

FindBack is a personal memory engine for everything you save online. Share any
post, video, article, recipe or product from any app, and FindBack fetches it,
understands it, extracts the useful parts, and makes it findable later through a
vague natural-language description such as *"that video about an AI tool for
making presentations"*.

The core loop is **Save → Find → Use** in under 15 seconds. Less a bookmark
manager, more a second brain.

---

## Overview

Saving is local-first and instant; the AI work happens afterwards in the
background. Search is hybrid — semantic (vectors) fused with keyword matching —
so it answers both "what was this about?" and "where was that thing called AWS?".

```
 ┌──────────────────────────────┐
 │ Flutter app                  │  share sheet / capture sheet
 │  SQLite mirror + write queue │  saves offline, syncs later
 └──────────────┬───────────────┘
                │  HTTPS  ·  POST /api/v1/ingest, /sync/batch
                │           GET  /api/v1/search, /items, /memories
                ▼
 ┌──────────────────────────────┐        ┌────────────────────────────┐
 │ FastAPI API  (:8000)         │ ─────► │ Redis + Celery worker      │
 │ auth · validation · search   │  jobs  │ fetch → extract → brief →  │
 └──────────────┬───────────────┘        │ chunk → embed              │
                │                        └──────────────┬─────────────┘
                ▼                                       │
 ┌──────────────────────────────────────────────────────┴─────────────┐
 │ PostgreSQL 16 + pgvector — content assets, per-user memories,     │
 │ chunks, HNSW vectors, full-text index, job state machine          │
 └────────────────────────────────────────────────────────────────────┘
```

**Design rules the code follows** (see `PRODUCT.md`): a save never waits for AI,
public content is processed once and reused, user notes/intents are always
private, search is by meaning rather than title, and no AI provider is hard-wired
into the app.

---

## Key Features

### Mobile (Flutter)

- **Offline-first capture** — a save is written to SQLite first (optimistic row
  plus queue entry), so it is searchable immediately and survives an app kill.
  The app-bar badge shows the pending queue depth.
- **Self-healing sync** — the queue drains on launch, on every connectivity
  transition and on a 30 s heartbeat, with exponential backoff from 1 s to a 5
  minute ceiling. A row the server refuses three times is parked instead of
  looping forever, and local ids are replaced by server ids once a save lands.
- **Share-sheet intake** — the Dart half of *Any app → Share → FindBack →
  Saved* is implemented over the `findback/share` method channel and a shared
  payload is queued exactly like a typed save, so a share taken with no signal
  is never lost (`docs/NATIVE_SHARE.md` covers the remaining native work).
- **Reads that degrade gracefully** — search, Recent and detail fall back to the
  local mirror and are labelled "Offline"; deletes are local first and the
  server copy is removed when reachable.
- **Search-first UI** — auto-focused search box, debounced queries, category
  chips, match reason on every result, detail page, capture sheet, and a
  keep-awake Cook Mode.
- **Build-time configuration** — everything (API base URL, Supabase URL, anon
  key, optional token) is passed with `--dart-define`; the binary reads no `.env`
  and ships no secrets.

### Backend / cloud services (FastAPI · Celery · Postgres)

- **REST API** — ingest, batch sync from the phone, hybrid search, paginated
  item list, item detail/delete, per-content user context (note + intent), and a
  `/health` endpoint that reports database/Redis state and the active retention
  policy.
- **Staged processing pipeline** — `FETCH → NORMALIZE → UNDERSTAND → BRIEF →
  CHUNK → EMBED`, each stage committing its own output and recording progress,
  so a retry resumes after the last stage that succeeded.
- **Durable job state machine** — jobs are claimed atomically, retried with
  backoff and written through an outbox, so a Redis outage delays work instead of
  losing it.
- **Content dedupe with privacy rules** — `ContentAsset` holds the content and
  may be shared, `UserMemory` holds one user's notes, intent and save count and
  is never shared. `PUBLIC` assets are reusable by anyone; `PRIVATE`/`UNKNOWN`
  only by their owner (`UNKNOWN` is treated as private).
- **Hybrid search** — pgvector cosine recall plus Postgres full-text recall,
  fused with Reciprocal Rank Fusion (`RRF_K`, `VECTOR_WEIGHT`, `BM25_WEIGHT`),
  chunk-level max score, and a human-readable `match_reason`.
- **Provider-agnostic AI gateway** — Groq, Gemini, OpenAI or any
  OpenAI-compatible endpoint for chat and embeddings, chosen in `.env`. With no
  keys the pipeline still runs: heuristic extraction and keyword-only search.
- **Privacy by default** — a log filter redacts saved content and secrets from
  every log record, raw fetched text is dropped when the pipeline finishes
  (`RAW_TEXT_RETENTION_HOURS`), and deleting one user's save never deletes the
  shared asset another user still holds.
- **Auth** — Supabase-compatible JWT verification, plus a local dev identity
  (`DEV_AUTH_ENABLED`) for development.
- **Schema management** — Alembic migrations `0001`–`0010`, applied on API
  startup through `SCHEMA_BOOTSTRAP` (or manually).

---

## Technology stack

| Layer | Technology |
| --- | --- |
| Mobile app | Flutter / Dart ≥ 3.4 — Dio, sqflite, connectivity_plus, flutter_secure_storage, wakelock_plus |
| API | FastAPI, Pydantic, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 16 + pgvector (HNSW indexes), `tsvector` full-text search |
| Queue / workers | Redis 7 + Celery, with a transactional outbox dispatcher |
| AI | Groq · Gemini · OpenAI (or compatible) behind one gateway; embeddings at 1536 dims |
| Content fetching | Firecrawl, Jina Reader, youtube-transcript-api, BeautifulSoup/lxml |
| Object storage | Optional S3/R2 snapshots via boto3 |
| Auth | Supabase JWT (python-jose) + local dev identity |
| Tooling | pytest, Flutter test, offline Recall@5 eval, Postman collection + OpenAPI spec |

---

## Repository structure

```
FindBack/
├── README.md               # this file
├── SETUP.md                # install, configure, run and verify everything
├── PRODUCT.md              # product rules the implementation must not break
├── AGENTS.md               # working rules for AI coding agents
├── docker-compose.yml      # Postgres + Redis + API + worker in one command
├── .env.example            # every backend/config variable, documented
│
├── backend/                # Python services
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic/            # migrations 0001 … 0010
│   ├── app/
│   │   ├── main.py         # FastAPI entry point (CORS, /health, routers)
│   │   ├── auth.py         # JWT verification + local dev identity
│   │   ├── database.py     # engine, sessions, schema bootstrap
│   │   ├── models.py       # User, ContentAsset, UserMemory, Item, Chunk, ProcessingJob
│   │   ├── schemas.py      # request/response models
│   │   ├── env.py          # the single place that reads .env
│   │   ├── celery_app.py   # Celery broker/backend
│   │   ├── tasks.py        # process_item worker task
│   │   ├── routers/        # ingest, search, items, user_context
│   │   ├── services/       # fetcher, extractor, embedder, ai, ai_gateway,
│   │   │                   # pipeline, search, outbox, identity, privacy,
│   │   │                   # retention, limits, profiles, storage
│   │   └── utils/          # canonical URL, dedupe key, text helpers
│   ├── scripts/            # check_ai.py, dispatch_outbox.py
│   └── tests/              # pytest suite (unit + optional live-Postgres tests)
│
├── mobile/                 # Flutter client (entry point: lib/main.dart)
│   ├── lib/
│   │   ├── main.dart       # app bootstrap: services → UI → sync → share
│   │   ├── app_services.dart   # composition root
│   │   ├── config.dart     # --dart-define configuration
│   │   ├── data/           # api_client, local_db (mirror + queue), token_store
│   │   ├── services/       # capture, items, sync, share_intent
│   │   ├── features/home/  # home screen, search controller, detail, capture sheet
│   │   └── models/         # item, search_result, json utils
│   ├── test/               # 65 Flutter tests (offline queue, sync, search, share)
│   ├── plugins/            # legacy Expo config plugin (reference for share intake)
│   ├── src/, App.tsx, app.json, package.json
│   │                       # legacy Expo/React Native client — superseded by Flutter
│   ├── pubspec.yaml
│   └── README.md           # client architecture and offline behaviour
│
├── tool/setup_mobile.sh    # generates android/ + ios/, then pub get/analyze/test
├── eval/                   # golden set + offline Recall@5 harness
├── docs/                   # PRD, ARCHITECTURE, OPERATIONS, NATIVE_SHARE
├── postman/                # collection, local environment, OpenAPI spec
└── .github/workflows/      # CI definition
```

`mobile/android/`, `mobile/ios/`, `backend/.venv/` and `.env` are generated
locally and deliberately not committed.

---

## Quick start

```bash
# 1. Configure (docker-compose requires .env to exist)
cp .env.example .env          # add GEMINI_API_KEY or GROQ_API_KEY for full AI

# 2. Start Postgres + Redis + API + worker
docker compose up --build     # use "docker-compose up --build" without the plugin

# 3. Check the API, then browse http://localhost:8000/docs
curl http://localhost:8000/health

# 4. Generate the native projects, then run the app
tool/setup_mobile.sh
cd mobile
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

`10.0.2.2` is how the Android emulator reaches the host machine's `localhost`;
use `http://localhost:8000` for the iOS simulator and your machine's LAN address
for a physical device.

Full prerequisites, every configuration variable, a Docker-free backend setup,
release builds, the end-to-end verification walkthrough and a troubleshooting
table are in **[SETUP.md](SETUP.md)**.

---

## API at a glance

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service status, database/Redis checks, retention policy |
| `GET` | `/docs` | Interactive Swagger UI |
| `POST` | `/api/v1/ingest` | Queue a URL for background processing |
| `POST` | `/api/v1/sync/batch` | Flush the phone's offline write queue |
| `GET` | `/api/v1/search?q=…` | Hybrid search (`category`, `limit`) |
| `GET` | `/api/v1/items` | Paginated list (`limit`, `cursor`) |
| `GET`/`DELETE` | `/api/v1/items/{id}` | Item detail / delete |
| `GET`/`PATCH`/`DELETE` | `/api/v1/memories/{content_id}` | This user's note and intent for a piece of content |

```bash
curl --get --data-urlencode 'q=chicken cream mushroom' http://localhost:8000/api/v1/search
```

All `/api/v1` routes require a Bearer token unless the backend runs with
`DEV_AUTH_ENABLED=true`.

---

## Validation

```bash
(cd backend && python -m pytest -q)        # unit suite; add TEST_DATABASE_URL for the live-DB tests
(cd mobile && flutter analyze && flutter test)
python eval/eval_offline.py                # Recall@5 gate (must reach 0.85)
```

Expected output for each command, plus the end-to-end mobile ↔ backend
walkthrough, is documented in [SETUP.md](SETUP.md).

---

## Documentation

| Document | What it covers |
| --- | --- |
| [SETUP.md](SETUP.md) | Prerequisites, environment configuration, running the backend and the app, verification |
| [PRODUCT.md](PRODUCT.md) | The product rules the implementation must not break |
| [mobile/README.md](mobile/README.md) | Client architecture, offline-first contract, what each service owns |
| [docs/PRD.md](docs/PRD.md) | Vision, personas, user stories, UX spec, metrics |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data model, AI pipeline, search algorithm |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Retention/deletion, log privacy, AI provider configuration, embedding-width changes |
| [docs/NATIVE_SHARE.md](docs/NATIVE_SHARE.md) | The remaining native work for Android/iOS share intake |
| [AGENTS.md](AGENTS.md) | Working agreement for AI coding agents on this repository |

Postman artifacts live under `postman/collections`, `postman/environments` and
`postman/specs`.

---

## Status

Implemented: offline save with a durable write queue, batch sync, background
ingest pipeline with a resumable stage machine, content dedupe and per-user
memory model, hybrid semantic + keyword search with RRF, privacy/retention
rules, and the Flutter client with search, detail and offline fallback.

Not yet implemented: the native share targets (Android `ACTION_SEND` filter and
the iOS Share Extension) and the sign-in UI — the app runs on the local dev
token path. See `docs/NATIVE_SHARE.md` and `mobile/README.md`.

## License

Private — all rights reserved.
