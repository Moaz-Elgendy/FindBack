# FindBack â€” AI-Powered Memory for the Internet

> **"You don't need to remember where you saved it. Just remember what you remember about it."**

FindBack is a mobile app that acts as your personal memory for everything you save online. Share any post, video, article, product or recipe â†’ it auto-summarizes, extracts key info, categorizes, and makes it findable via vague natural language: *"that video about an AI tool for making presentations"*.

**Core Loop:** `Save â†’ Find â†’ Use` in <15 seconds. Less bookmark manager, more second brain.

## Quick Start

```bash
# 1. Clone & env
cp .env.example .env
# edit OPENAI_API_KEY, FIRECRAWL_API_KEY

# 2. Infra (Postgres+pgvector, Redis, API, Worker)
docker compose up --build

# 3. Mobile (Flutter)
cd mobile
../tool/setup_mobile.sh     # generates android/ + ios/, then pub get + analyze + test
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
# Share any URL â†’ FindBack â†’ Saved âœ“
```

`10.0.2.2` is how the Android emulator reaches the host; use `http://localhost:8000`
for the iOS simulator. See `mobile/README.md` for the offline queue and share-intake details.

API: http://localhost:8000/docs
Search: `GET /api/v1/search?q=chicken%20cream%20mushroom`

## Repo Structure

```
FindBack/
â”œâ”€â”€ docs/
â”‚   â”œâ”€â”€ PRD.md           # Product spec (user stories, UX, metrics)
â”‚   â””â”€â”€ ARCHITECTURE.md  # System design, data model, AI pipeline
â”œâ”€â”€ backend/             # FastAPI + pgvector + Celery + Redis
â”‚   â”œâ”€â”€ app/
â”‚   â”‚   â”œâ”€â”€ main.py
â”‚   â”‚   â”œâ”€â”€ models.py
â”‚   â”‚   â”œâ”€â”€ schemas.py
â”‚   â”‚   â”œâ”€â”€ database.py
â”‚   â”‚   â”œâ”€â”€ routers/
â”‚   â”‚   â””â”€â”€ services/
â”‚   â””â”€â”€ requirements.txt
â”œâ”€â”€ mobile/              # Flutter client (offline SQLite queue + sync)
â”‚   â”œâ”€â”€ lib/
â”‚   â”‚   â”œâ”€â”€ data/        # api_client.dart, local_db.dart, token_store.dart
â”‚   â”‚   â”œâ”€â”€ services/    # capture / items / sync + app_services.dart
â”‚   â”‚   â””â”€â”€ features/home/
â”‚   â””â”€â”€ test/
â”œâ”€â”€ tool/
â”‚   â””â”€â”€ setup_mobile.sh  # flutter create + pub get + analyze + test
â”œâ”€â”€ eval/
â”‚   â””â”€â”€ golden.json      # 50 items + 100 vague queries for Recall@5
â”œâ”€â”€ docker-compose.yml
â””â”€â”€ .env.example
```

## Status
MVP skeleton â€” Save (Share Extension + offline queue) + AI Ingest (Fetchâ†’Extractâ†’Embed) + Hybrid Semantic Search (vector + BM25 + RRF).

See `docs/PRD.md`, `docs/ARCHITECTURE.md`, and `docs/OPERATIONS.md` for product, architecture, deployment, native signing, and validation guidance.

## Validation

```bash
cd backend && python -m pytest -q
cd ../mobile && flutter analyze && flutter test
cd .. && python eval/eval_offline.py
```

Postman artifacts are under `postman/collections`, `postman/environments`, and `postman/specs`.

## License
Private â€” All rights reserved.

