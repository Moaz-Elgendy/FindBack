# FindBack — AI-Powered Memory for the Internet

> **"You don't need to remember where you saved it. Just remember what you remember about it."**

FindBack is a mobile app that acts as your personal memory for everything you save online. Share any post, video, article, product or recipe → it auto-summarizes, extracts key info, categorizes, and makes it findable via vague natural language: *"that video about an AI tool for making presentations"*.

**Core Loop:** `Save → Find → Use` in <15 seconds. Less bookmark manager, more second brain.

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
# Share any URL → FindBack → Saved ✓
```

`10.0.2.2` is how the Android emulator reaches the host; use `http://localhost:8000`
for the iOS simulator. See `mobile/README.md` for the offline queue and share-intake details.

API: http://localhost:8000/docs
Search: `GET /api/v1/search?q=chicken%20cream%20mushroom`

## Repo Structure

```
FindBack/
├── docs/
│   ├── PRD.md           # Product spec (user stories, UX, metrics)
│   └── ARCHITECTURE.md  # System design, data model, AI pipeline
├── backend/             # FastAPI + pgvector + Celery + Redis
│   ├── app/
│   │   ├── main.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── database.py
│   │   ├── routers/
│   │   └── services/
│   └── requirements.txt
├── mobile/              # Flutter client (offline SQLite queue + sync)
│   ├── lib/
│   │   ├── data/        # api_client.dart, local_db.dart, token_store.dart
│   │   ├── services/    # capture / items / sync + app_services.dart
│   │   └── features/home/
│   └── test/
├── tool/
│   └── setup_mobile.sh  # flutter create + pub get + analyze + test
├── eval/
│   └── golden.json      # 50 items + 100 vague queries for Recall@5
├── docker-compose.yml
└── .env.example
```

## Status
MVP skeleton — Save (Share Extension + offline queue) + AI Ingest (Fetch→Extract→Embed) + Hybrid Semantic Search (vector + BM25 + RRF).

See `docs/PRD.md`, `docs/ARCHITECTURE.md`, and `docs/OPERATIONS.md` for product, architecture, deployment, native signing, and validation guidance.

## Validation

```bash
cd backend && python -m pytest -q
cd ../mobile && flutter analyze && flutter test
cd .. && python eval/eval_offline.py
```

Postman artifacts are under `postman/collections`, `postman/environments`, and `postman/specs`.

## License
Private — All rights reserved.
