# FindBack — Setup Guide

Everything needed to get the repository running on a developer machine: the
backend and its services, the Flutter app on an emulator or device, and how to
verify that the two actually talk to each other.

When you are finished you will have:

| Service | Address | Started by |
| --- | --- | --- |
| FindBack API (FastAPI) | <http://localhost:8000> | `docker compose` or `uvicorn` |
| Swagger UI | <http://localhost:8000/docs> | the API |
| PostgreSQL 16 + pgvector | `localhost:5432` | `docker compose` or your own server |
| Redis 7 (job queue) | `localhost:6379` | `docker compose` or `redis-server` |
| Celery worker | — | `docker compose` or `celery worker` |
| Outbox dispatcher | — | `docker compose` or `python -m scripts.dispatch_outbox` |
| Flutter app | emulator / device | `flutter run` |

---

## 1. Prerequisites

### 1.1 Required tools

| Tool | Why | Notes |
| --- | --- | --- |
| **Git + a Bash shell** | repository scripts (`tool/setup_mobile.sh`) | Windows: use WSL2 or Git Bash; macOS/Linux: native |
| **Docker Engine with the Compose v2 plugin** | the recommended way to run the whole backend | verify with `docker compose version`. A Docker-free path is in [§3 Option B](#option-b--local-runtime-no-docker) |
| **Flutter (stable channel)** | building and running the mobile app | Dart **≥ 3.4** (Flutter 3.22 or newer). Verified locally with Flutter 3.47 / Dart 3.13 |
| **Android Studio** (Android SDK + an emulator) | Android builds and runs | Xcode is additionally required for iOS, and only exists on macOS |
| **Python 3.11 or 3.12** | backend, if you skip Docker; also the eval scripts | The `Dockerfile` uses 3.11 and the repo's own virtualenv uses 3.12. Newer interpreters are not covered by the pinned `requirements.txt` |

If you skip Docker you also need **PostgreSQL 16 with the pgvector extension**
(the image `pgvector/pgvector:pg16` provides both) and **Redis 7**.

### 1.2 Optional tools

| Tool | Why you might want it |
| --- | --- |
| **An AI provider key** (Groq, Google Gemini or OpenAI) | Real LLM extraction, Briefs and vector search. Without one the pipeline still runs with heuristic extraction and keyword-only search |
| **Firecrawl key / Jina Reader** | Full-page fetching; disabled by default, preview text is used instead |
| **S3 or R2 credentials** (boto3 is already a dependency) | Persisting raw page snapshots. AWS CLI is *not* required — the backend only reads `S3_*` environment variables |
| **Node.js 18+** | Only for the legacy Expo/React Native client in `mobile/` and for the Postman CLI. The Flutter app does not use Node |
| **Postman** | Importing the bundled collection, environment and OpenAPI spec |
| **uv** | Faster virtualenv creation (`uv venv`, `uv pip install -r requirements.txt`); plain `venv` + `pip` works too |

### 1.3 Ports and generated files

The default configuration expects these ports to be free: **5432** (Postgres),
**6379** (Redis), **8000** (API). If another PostgreSQL or Redis already uses
them, either stop it or remap the ports and update `DATABASE_URL` /
`REDIS_URL` accordingly.

These paths are generated locally and are git-ignored — do not expect them in a
fresh clone:

| Path | Produced by |
| --- | --- |
| `.env` | you, from `.env.example` |
| `backend/.venv/` | your Python environment |
| `mobile/android/`, `mobile/ios/` | `tool/setup_mobile.sh` |

---

## 2. Environment configuration

### 2.1 Create the backend `.env`

```bash
cp .env.example .env
```

PowerShell: `Copy-Item .env.example .env`

`docker-compose.yml` declares `env_file: .env` for both the API and the worker,
so **this file must exist before `docker compose up`** — the command fails
otherwise. `.env` is git-ignored; never commit it.

The backend reads the first `.env` it finds in `backend/app/`, `backend/`, the
repository root, or the current working directory (`backend/app/env.py`). Real
process environment variables always win over the file, which is what
`docker compose` relies on.

### 2.2 Variable reference

**Core (defaults are fine for local development)**

| Variable | Default / example | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://findback:findback@postgres:5432/findback` | Compose overrides this to the `postgres` service; a local runtime must use `localhost` |
| `REDIS_URL` | `redis://redis:6379/0` | Celery broker and result backend |
| `API_SECRET_KEY` | change it | Secret used to verify tokens when `SUPABASE_JWT_SECRET` is unset; set your own 32+ character value |
| `DEV_AUTH_ENABLED` | `true` | **Local only.** Lets requests without a token use the dev identity. Must be `false` in production |
| `DEV_AUTH_EMAIL` | `dev@findback.local` | Address of that dev identity |
| `JWT_AUDIENCE` | `authenticated` | Audience claim expected in Supabase JWTs |
| `CORS_ORIGINS` | `http://localhost:8081` | Comma-separated allowed origins |
| `SCHEMA_BOOTSTRAP` | `migrate` | `migrate` runs `alembic upgrade head` on startup, `create` builds the schema from models (throwaway databases only), `none` leaves it to you |

**AI providers** (see `docs/OPERATIONS.md` for the full behaviour)

| Variable | Purpose |
| --- | --- |
| `AI_PROVIDER` | `groq`, `gemini`, `openai` or `openai_compatible`. Leave empty to auto-detect from the first key present, in the order Groq → Gemini → OpenAI |
| `GROQ_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY` | Provider credentials. One chat key is enough to enable extraction |
| `OPENAI_BASE_URL`, `OPENAI_COMPATIBLE_API_KEY` | Point the OpenAI client at Ollama, vLLM, OpenRouter, etc. |
| `EXTRACTOR_MODEL` | Override the chat model id when the provider retires a default |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Embeddings are resolved separately: **Groq cannot serve the 1536-wide vector column**, so a Groq-only setup should set Gemini (or OpenAI) for embeddings, or run with keyword search |
| `EMBEDDING_DIMS` | Must stay `1536`, matching `Vector(1536)` in `app/models.py` and migration `0001` |
| `AI_TIMEOUT_SECONDS`, `AI_MAX_RETRIES`, `AI_USE_JSON_MODE`, `EXTRACTOR_MAX_TOKENS`, `EXTRACTOR_MAX_CHARS`, `EMBED_MAX_CHARS` | Reliability and cost knobs |
| `AI_DEBUG` | Logs full provider request/response bodies — they contain saved page text, so leave it `false` |

**Fetching, storage, search, retention**

| Variable | Purpose |
| --- | --- |
| `FIRECRAWL_API_KEY` | Optional: enables the Firecrawl step of the fetch chain. The chain is YouTube transcript → Firecrawl (key required) → Jina Reader (free, no key) → preview text fallback |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` | Optional raw-snapshot storage. Leaving them empty simply skips the snapshot |
| `SUPABASE_URL`, `SUPABASE_JWT_SECRET` | JWT verification when `DEV_AUTH_ENABLED=false` |
| `RRF_K`, `VECTOR_WEIGHT`, `BM25_WEIGHT` | Hybrid-search fusion (defaults `60`, `0.7`, `0.3`) |
| `RAW_TEXT_RETENTION_HOURS` | How long fetched text is kept after processing. `0` drops it as soon as the pipeline reaches `READY` (the default) |

`JINA_READER_ENABLED` also appears in `.env.example`; the fetcher does not read
it today, so setting it has no effect.

### 2.3 Mobile configuration (`--dart-define`)

The Flutter binary reads **no `.env` file** — configuration is compiled in from
`--dart-define` (`mobile/lib/config.dart`):

| Define | Default | Meaning |
| --- | --- | --- |
| `API_BASE_URL` | `http://localhost:8000` | API root, no trailing slash |
| `API_TOKEN` | unset | Seeds the token store when the backend runs with `DEV_AUTH_ENABLED=false` |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | unset | Public Supabase values; the sign-in flow is not wired up yet |
| — | — | Only public values belong here. Never bake a secret into a binary |

### 2.4 Authentication modes

- **Development** — `DEV_AUTH_ENABLED=true`: requests without an
  `Authorization` header are accepted and attributed to the dev identity, so the
  phone app, curl and Postman all work with no token.
- **JWT** — `DEV_AUTH_ENABLED=false`: every `/api/v1` request needs
  `Authorization: Bearer <token>` verified against `SUPABASE_JWT_SECRET`. Pass a
  token to the app with `--dart-define=API_TOKEN=<token>`.
- **Production checklist** (from `docs/OPERATIONS.md`): `DEV_AUTH_ENABLED=false`,
  a real `SUPABASE_JWT_SECRET`, and a restrictive `CORS_ORIGINS`.

---

## 3. Backend and services setup

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env          # once; add an AI key if you have one
docker compose up --build     # use "docker-compose up --build" without the plugin
```

| Service | Image / command | Published port |
| --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` (creates `findback`, loads `backend/init.sql`) | 5432 |
| `redis` | `redis:7-alpine` | 6379 |
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | 8000 |
| `worker` | `celery -A app.celery_app.celery worker --loglevel=info` | — |

The API waits for the database and Redis healthchecks, the worker waits for them
to start, both mount `backend/` for live code, and both receive your `.env`.

**Schema.** With the default `SCHEMA_BOOTSTRAP=migrate`, the API applies all
Alembic migrations on startup. To apply them by hand:

```bash
docker compose exec api alembic upgrade head
```

**Everyday commands**

```bash
docker compose logs -f api worker outbox   # API, worker and dispatcher
docker compose ps                    # service state
docker compose down                  # stop (data stays in the pgdata volume)
docker compose down -v               # stop and delete the database volume
```

### Option B — local runtime (no Docker)

**1. PostgreSQL with pgvector** — either reuse the container image:

```bash
docker run -d --name fbpg16 -p 5432:5432 \
  -e POSTGRES_USER=findback -e POSTGRES_PASSWORD=findback -e POSTGRES_DB=findback \
  pgvector/pgvector:pg16
```

or use an existing server and create the database:

```bash
psql -U postgres -c "CREATE ROLE findback LOGIN PASSWORD 'findback' SUPERUSER;"
psql -U postgres -c "CREATE DATABASE findback OWNER findback;"
```

If port 5432 is already taken, publish a different one (for example
`-p 55432:5432`) and put that port in `DATABASE_URL`.

**2. Redis:**

```bash
docker run -d --name fbredis -p 6379:6379 redis:7-alpine
# or: redis-server
```

**3. Python environment:**

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate        # Windows: backend\.venv\Scripts\activate
pip install -r backend/requirements.txt
```

**4. Run the API** (separate terminal, same environment activated):

```bash
export DATABASE_URL=postgresql://findback:findback@localhost:5432/findback
export REDIS_URL=redis://localhost:6379/0
export DEV_AUTH_ENABLED=true

cd backend
uvicorn app.main:app --reload --port 8000
```

**5. Run the worker** (third terminal, same env, still in `backend/`):

```bash
celery -A app.celery_app.celery worker --loglevel=info
```

**6. Outbox dispatcher.** If Redis was down when a save arrived, the publish
failed but the intent was written to the database. Nothing else can recover it:
a failed publish leaves no Celery message anywhere, so the job would sit
`PENDING` forever. The dispatcher republishes it.

`docker compose up` starts it as the `outbox` service, so there is nothing to
run by hand. Its tick length is `OUTBOX_DISPATCH_INTERVAL_SECONDS` (default 5):

```bash
OUTBOX_DISPATCH_INTERVAL_SECONDS=30 docker compose up outbox
```

Without Docker, run it yourself — it should stay up alongside the worker:

```bash
cd backend
python -m scripts.dispatch_outbox --once            # single tick
python -m scripts.dispatch_outbox                   # loop, interval from .env
python -m scripts.dispatch_outbox --interval 30     # override the interval
```

The API is usable on its own: with Redis or the worker missing, `/health`
reports `"redis":"down"` while `"status"` stays `"ok"`, ingest still answers
HTTP 200 with `"status":"pending"`, and pending items are processed once the
queue is back.

### AI provider connectivity check

```bash
cd backend
python scripts/check_ai.py                  # resolved config + live chat ping
python scripts/check_ai.py --list-models    # model ids the provider currently offers
python scripts/check_ai.py --embeddings     # embed a probe and report the vector width
```

Exit code is `0` on success, so it can gate a CI job.

| Situation | Result |
| --- | --- |
| No chat key | Extraction uses the offline heuristic; everything else works |
| No embedding key | Search falls back to keyword recall (BM25/text), still functional |
| Model id retired by the provider | The error names it — set `EXTRACTOR_MODEL` or `EMBEDDING_MODEL` in `.env`, no code change |

### Inspecting the API contract

- **Swagger UI:** <http://localhost:8000/docs> — try requests interactively.
- **Root index:** <http://localhost:8000/> returns the service name and links.
- **OpenAPI spec:** `postman/specs/FindBack API/index.yaml`.
- **Postman:** import `postman/collections/FindBack API` together with the
  `postman/environments/FindBack Local` environment (`base_url` is
  `http://localhost:8000`, `access_token` stays empty while dev auth is on).
  The collection covers health, ingest, search, list, get and delete.

There is **no mock server** in this repository, and none is needed: the Flutter
tests inject function doubles directly into the services, and the backend test
suite clears every provider key in `conftest.py` so no test can reach an AI
provider or the network.

---

## 4. Mobile app setup and inspection

### 4.1 One-time bootstrap

`mobile/android/` and `mobile/ios/` are **not checked in** — they are generated
per machine. From the repository root:

```bash
tool/setup_mobile.sh
```

The script generates the platform projects in a temporary directory (so the
hand-written `lib/` and `pubspec.yaml` are never overwritten by templates),
copies only `android/` and `ios/` into `mobile/`, then runs `flutter pub get`,
`flutter analyze` and `flutter test`. Re-running it is safe.

Options:

```bash
FINDBACK_ORG_ID=com.example tool/setup_mobile.sh       # different bundle id
FINDBACK_EXTRA_PLATFORMS=linux tool/setup_mobile.sh    # also generate desktop
```

On Windows, run it from WSL or Git Bash — it is a Bash script.

### 4.2 Install dependencies

```bash
cd mobile
flutter pub get
flutter analyze
flutter test
```

### 4.3 Run on an emulator, simulator or device

```bash
flutter devices                 # connected devices and emulators
flutter emulators               # available Android virtual devices
```

```bash
# Android emulator (10.0.2.2 reaches the host machine's localhost)
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000

# iOS simulator on macOS (localhost already points at the host)
flutter run --dart-define=API_BASE_URL=http://localhost:8000

# Physical Android device over USB or the same Wi-Fi network
flutter run --dart-define=API_BASE_URL=http://192.168.1.50:8000
```

Replace `192.168.1.50` with your machine's LAN IP. When more than one device is
connected, pick one by adding `-d` followed by the id printed by
`flutter devices`. Start the backend first — see [§3](#3-backend-and-services-setup).

If the backend runs with `DEV_AUTH_ENABLED=false`, seed the token store:

```bash
export TOKEN="the-jwt-issued-by-your-auth-provider"
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000 \
            --dart-define=API_TOKEN="$TOKEN"
```

While the app is running: `r` hot reloads, `R` hot restarts, `q` quits.

### 4.4 Development vs production builds

**Development** — debug build, recompiles on change:

```bash
flutter run --debug --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

**Production** — release build pointed at your deployed API:

```bash
# Android APK / Play Store bundle
flutter build apk    --release --dart-define=API_BASE_URL=https://api.example.com
flutter build appbundle --release --dart-define=API_BASE_URL=https://api.example.com

# iOS (requires an Apple signing team configured in Xcode)
flutter build ipa    --release --dart-define=API_BASE_URL=https://api.example.com
```

Artifacts land in `mobile/build/`. To keep one file per environment instead of
long command lines, put the defines in JSON and pass
`--dart-define-from-file=dev.json` (for example
`{ "API_BASE_URL": "http://10.0.2.2:8000" }`).

### 4.5 Inspecting and debugging the app

| Task | Command |
| --- | --- |
| Static analysis (must be clean before committing) | `cd mobile && flutter analyze` |
| Unit tests (offline queue, sync, search, share) | `flutter test` |
| Runtime logs from a device | `flutter logs` |
| Attach to an already-running app | `flutter attach` (add `-d` to choose a device) |
| Performance run | `flutter run --profile` |
| Inspect the local database | SQLite browser against the app's sandbox database, or step through `test/local_db_test.dart`, which runs the real SQL on the desktop |

In the running app: the app-bar badge is the number of captures still waiting to
be uploaded, and the label "Offline" means results came from the on-device
mirror rather than the API.

### 4.6 Known gaps

- **Native share intake** is not wired up yet: the Android `ACTION_SEND`
  intent-filter and the iOS Share Extension target must be added to the
  generated projects. The Dart side and its protocol are finished — see
  `docs/NATIVE_SHARE.md`.
- **Sign-in UI** does not exist; the app uses the dev token path.
- The Expo/React Native sources in `mobile/src/`, `mobile/App.tsx` and
  `mobile/package.json` are the **superseded** first client. Only the Flutter
  app is maintained.

---

## 5. Verification and testing

### 5.1 Automated suites

Run these after any change. The output below is what the suites produced when
this guide was written.

| Suite | Command | Expected result |
| --- | --- | --- |
| Backend unit tests | `cd backend && python -m pytest -q` | `272 passed, 272 skipped` — the skips are the live-PostgreSQL tests |
| Backend tests **with** a database | `cd backend && TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/findback python -m pytest -q` | `544 passed` (takes ~3 minutes; point the URL at the PostgreSQL you started, and note the tests create and drop throwaway `fb_*` databases, so the role must be allowed to `CREATE DATABASE`) |
| Backend syntax check | `python -m compileall backend/app backend/alembic` | compiles with no errors |
| Flutter analysis | `cd mobile && flutter analyze` | `No issues found!` |
| Flutter tests | `cd mobile && flutter test` | `All tests passed!` (65 tests) |
| Search-quality gate | `python eval/eval_offline.py` from the repository root | `Recall@5=1.000 MRR=1.000 queries=5` — exits non-zero below `Recall@5 = 0.85` |

`eval/eval_offline.py` uses only the Python standard library, so any Python 3
will run it. `eval/eval.py` is the stub for a live-API evaluation.

### 5.2 Backend end-to-end

With the stack running (§3):

```bash
# 1. Health — expect "status":"ok", "db":"ok", "redis":"ok"
curl -s http://localhost:8000/health

# 2. Save something
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://en.wikipedia.org/wiki/Mushroom_risotto"}'
# -> {"id":"a6a1e5f4-...","status":"pending","canonical_url":"..."}

# 3. Watch it move from pending to ready (the worker is what advances it)
curl -s 'http://localhost:8000/api/v1/items?limit=5'

# 4. Search it by meaning
curl -s --get --data-urlencode 'q=creamy mushroom pasta' \
  http://localhost:8000/api/v1/search
# -> {"results":[{"id":"...","match_reason":"...","score":...}],"took_ms":...}
```

Add `-H "Authorization: Bearer $TOKEN"` to steps 2–4 when
`DEV_AUTH_ENABLED=false`. Steps 2–4 also work from the Swagger UI at
`/docs`.

### 5.3 Mobile ↔ backend end-to-end

1. Start the backend (§3) and confirm `curl http://localhost:8000/health`
   answers `ok`.
2. Launch the app against it — Android emulator:
   ```bash
   cd mobile
   flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
   ```
3. In the capture sheet, paste a URL and save. You should get an immediate
   confirmation and the pending badge in the app bar; the badge disappears once
   the queue drains (`POST /api/v1/sync/batch`).
4. Search for the item with an approximate phrase rather than the title, and
   confirm the result card carries a `match_reason`.
5. Open the detail page, then delete the item and confirm it disappears from
   both the app and `GET /api/v1/items`.
6. **Offline check:** stop the API (`Ctrl+C` or `docker compose stop api`),
   save another URL — it is stored locally and the badge increments — then start
   the API again and watch the badge drain within about 30 seconds.
7. **Search offline:** with the API down, searching still returns results from
   the SQLite mirror, labelled "Offline".

### 5.4 Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `env file .env not found` from `docker compose` | Create it first: `cp .env.example .env` |
| `port is already allocated` for 5432 / 6379 / 8000 | Another Postgres, Redis or API is running — stop it or change the published port and the matching `DATABASE_URL` / `REDIS_URL` |
| `401 Unauthorized` on `/api/v1/...` | The backend runs with `DEV_AUTH_ENABLED=false`; send a Bearer token or start it with dev auth |
| `/health` shows `"redis":"down"` and items stay `pending` | Redis or the worker is not running; start them. If `docker compose ps` does not show `outbox` running, start it — until it ticks, a save stranded by the outage stays `pending` |
| Ingest works but search returns `[]` although items are `ready` | No embedding provider is configured; set `GEMINI_API_KEY` (or another embedding-capable key), or accept keyword-only results |
| `Unable to locate asset ... android/` or "no application found" | `mobile/android/` has not been generated — run `tool/setup_mobile.sh` |
| Emulator cannot reach the API | Use `http://10.0.2.2:8000` (Android) or `http://localhost:8000` (iOS simulator), not `localhost` on Android |
| Physical iOS device refuses plain HTTP | iOS blocks non-TLS connections except to localhost and the generated project adds no ATS exception — use the simulator locally, or put TLS in front of the API for device testing |
| Backend tests report `222 skipped` | Expected: set `TEST_DATABASE_URL` to run them against a real PostgreSQL |
| Flutter fails inside WSL with ``env: `bash\r` `` | Shell scripts in the Flutter SDK (a git clone) were checked out with CRLF endings; check the SDK out again with `core.autocrlf=false`, or run Flutter from a native install. `.gitattributes` in this repository documents the same rule for repo scripts |
| Database says `expected 1536 dimensions` | `EMBEDDING_DIMS` does not match the `Vector(1536)` column — see "Changing the embedding width" in `docs/OPERATIONS.md` |
