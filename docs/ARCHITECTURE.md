# FindBack — Architecture Document

**Stack (MVP):** Expo React Native (TS) + FastAPI (Python) + Postgres 16 + pgvector + Redis + Groq/Gemini/OpenAI (pluggable, extraction + embeddings) + Firecrawl/Jina

## 1. System Overview

```
[Expo App + Share Extension + SQLite Queue] --HTTPS--> [FastAPI API] --> [Celery+Redis Queue] --> [Workers: Fetcher → LLM → Embedder] --> [Postgres+pgvector / R2]
```

**Key principle:** Save is optimistic & local-first; AI is async. Search is hybrid (vector + BM25, RRF) with rerank.

## 2. Mobile Architecture (Expo)

- **Share targets:** iOS Share Extension (NSExtensionItem, App Groups shared container), Android intent-filter ACTION_SEND in AndroidManifest.xml. Extension is lightweight: writes {url, title, preview, timestamp, client_id} to shared SQLite (expo-sqlite + WatermelonDB), shows Saved ✓, exits. No network in extension.
- **Local DB:** WatermelonDB or expo-sqlite with tables `items` (lite copy) + `sync_queue(client_id, url, preview_json, retries, status, created_at)`. Survives app kill.
- **Sync:** NetInfo listener + expo-background-fetch / WorkManager. POST /sync/batch flushes queue with exponential backoff (1s,5s,30s,5m). Server dedupes by canonical_url per user, returns {client_id→server_id}. Client patches.
- **Search offline:** WHERE title/tags LIKE %query% (lite). Banner "Offline — lite results". Online: call GET /search.
- **UI:** Home is Search (auto-focus). Result cards below. Detail is bottom sheet (gorhom/bottom-sheet). Cook Mode keeps screen awake.

## 3. Backend Architecture (FastAPI)

```
backend/app/
  main.py          # FastAPI app, CORS, /health, routers
  database.py      # SQLAlchemy async engine, session
  models.py        # SQLAlchemy models + pgvector
  schemas.py       # Pydantic request/response
  routers/
    ingest.py      # POST /ingest, POST /sync/batch
    search.py      # GET /search
    items.py       # GET /items, GET /items/:id, DELETE
  services/
    canonical.py   # url -> canonical_url (strip utm, lower, sort query, remove hash)
    fetcher.py     # Firecrawl/Jina/Apify/yt-transcript strategy chain
    extractor.py   # LLM JSON extraction (temp 0.1, validated)
    ai.py          # provider-agnostic chat/embeddings: Groq, Gemini, OpenAI
    embedder.py    # memory_string/chunk builders + provider embeddings via ai.py
    search.py      # hybrid query, RRF, rerank
    storage.py     # R2/S3 raw snapshot
  tasks.py         # Celery tasks: process_item(item_id)
```

### API Contract

```
POST /api/v1/ingest {url, preview?} -> {id, status: "processing"}  (auth required)
POST /api/v1/sync/batch {items: [{client_id, url, captured_at, preview}]} -> {mapped: [{client_id, id, status}], errors: []}
GET  /api/v1/search?q=...&category=&limit=10 -> {results: [{id, title, summary, tags, category, thumbnail, source_domain, match_reason, score, created_at}], took_ms}
GET  /api/v1/items?limit=20&cursor= -> {items: [], next_cursor}
GET  /api/v1/items/:id -> full item + chunks
DELETE /api/v1/items/:id -> 204
GET  /health -> {status, db, redis}
```

## 4. Data Model

### Postgres

```sql
-- enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TYPE item_status AS ENUM ('pending','processing','ready','failed');

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE NOT NULL,
  auth_provider text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  url text NOT NULL,
  canonical_url text NOT NULL,
  title text,
  title_clean text,
  source_domain text,
  source_type text, -- youtube, tiktok, instagram, article, recipe, product, tool
  thumbnail_url text,
  raw_s3_key text,
  summary text,
  key_points jsonb DEFAULT '[]',
  category text,
  entities jsonb DEFAULT '{}', -- {ingredients:[], tech:[], people:[], topics:[], products:[]}
  intent text, -- learn, cook, buy, watch
  tags text[] DEFAULT '{}',
  status item_status DEFAULT 'pending',
  last_seen_at timestamptz DEFAULT now(),
  created_at timestamptz DEFAULT now(),
  processed_at timestamptz,
  embedding vector(1536)  -- for short content; long content uses chunks
);

CREATE UNIQUE INDEX items_user_canonical_idx ON items(user_id, canonical_url);
CREATE INDEX items_embedding_hnsw ON items USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
CREATE INDEX items_user_created_idx ON items(user_id, created_at DESC);
CREATE INDEX items_category_idx ON items(category);

CREATE TABLE chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  item_id uuid NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  chunk_idx int NOT NULL,
  chunk_text text NOT NULL,
  embedding vector(1536) NOT NULL
);
CREATE INDEX chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_cosine_ops);

-- Hybrid: GIN for BM25-ish text search
ALTER TABLE items ADD COLUMN tsv tsvector GENERATED ALWAYS AS (
  to_tsvector('english', coalesce(title_clean,'') || ' ' || coalesce(summary,'') || ' ' || array_to_string(tags,' '))
) STORED;
CREATE INDEX items_tsv_idx ON items USING GIN (tsv);
```

Mobile SQLite mirrors `items` (id, title, summary, tags, category, thumbnail, status, created_at) + `sync_queue`.

## 5. AI Pipeline Detail

**Fetcher chain (try in order, return first success):**
1. YouTube → youtube-transcript-api; if no transcript → yt-dlp + Whisper (optional, deferred).
2. TikTok/IG → oEmbed for metadata → Apify actor (if FIRECRAWL key set, use Firecrawl scrape).
3. Articles/Products → Firecrawl else Jina Reader (`https://cc.jina.ai/http://URL`) → Puppeteer fallback.
Raw HTML/markdown stored to R2/S3 for reprocessing. Cost-capped via global canonical_url cache: reuse embedding if URL already ingested globally.

**Extractor (single LLM JSON call, temp 0.1, provider pluggable):**
System prompt returns strict JSON schema: `{summary:25w, key_points[3], category enum, entities{ingredients,tech,people,topics,products}, intent enum, tags[3], title_clean}`. Validate with Pydantic; on failure retry once, else status=failed but raw preserved.

**Memory string for embedding:** `title_clean + " " + summary + " " + join(key_points) + " " + join(flatten(entities))` — more matchable than raw transcript. Embedded via the configured model (default `gemini-embedding-001`, 1536d). Long content: chunk 800 tokens, 20% overlap, store in `chunks`; search aggregates max(chunk_score).

## 6. Search Algorithm (Hybrid + RRF + Rerank)

1. Embed query with same model → query_vector.
2. Vector top-50: `ORDER BY embedding <=> query_vector LIMIT 50` (or chunks max).
3. BM25 top-50: `WHERE tsv @@ plainto_tsquery('english', :q) ORDER BY ts_rank(tsv, plainto_tsquery) DESC`.
4. Merge via Reciprocal Rank Fusion: `score = wV * 1/(k+rankV) + wB * 1/(k+rankB)` with k=60, wV=0.7, wB=0.3. Items missing from one list get 0 for that term.
5. Rerank top-20 with Cohere rerank-v3 (or cross-encoder) if COHERE_API_KEY set; else sort by RRF score.
6. Return top-10 with `match_reason` derived from high-overlap entities/tags.

Why hybrid: vector captures gist ("crash fix"≈"recover from failure"), BM25 captures exact entities ("AWS", "mushroom"). RRF is simple, no tuning.

## 7. Offline-First Protocol

- Share always writes local first; UI shows Saved ✓ without awaiting network.
- Try POST /sync/batch immediately; on failure schedule WorkManager (Android 15m) / BGTaskScheduler (iOS). Backoff 1s→5s→30s→5m. Server normalizes canonical_url (strip utm_*, fbclid, lower host, sort query, remove fragment), dedupes per user.

## 8. Auth & Security

- Supabase Auth or Clerk (JWT). FastAPI dependency `get_current_user` verifies JWT via `SUPABASE_JWT_SECRET`.
- TLS; Postgres RLS optional — enforce `user_id = auth.uid()` at app layer for MVP.
- Private by default: content never used to train public models. Delete cascades embeddings.

## 9. Infra & Dev

- `docker-compose.yml`: postgres:16 + pgvector, redis:7, api (FastAPI uvicron), worker (Celery). Volumes for pgdata. Healthchecks.
- Env via `.env` (see `.env.example`). Cost per active user (100 saves/mo): ~$0.20 LLM + $0.05 embed + $0.10 scrape → >90% margin at $6 Pro.
- Observability: /health, structured logs, Celery flower optional.

## 10. Migration Path

- Start pgvector; if >200k items or p95 search >800ms, migrate vectors to Qdrant (dual-write, then cutover). Embeddings versioned (`embedding_model` col) for re-embed jobs.

