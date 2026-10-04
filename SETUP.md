# FindBack — Setup Guide

FindBack saves a link and works out what it was, so you can find it again later
from a vague memory instead of the title.

This guide gets the whole thing running, in whichever shape suits you, and then
proves it works. Every command here was run against this repository; anything I
could not run is marked **not verified**.

Three things run behind the API, and all three have to be up for a save to
finish:

| Piece | What it does | Started by |
| --- | --- | --- |
| **API** (FastAPI) | accepts saves, serves reads and search | `docker compose up` or `uvicorn` |
| **Worker** (Celery) | fetches the page, calls the model, writes the brief | `docker compose up` or `celery worker` |
| **Outbox dispatcher** | republishes work a queue outage stranded | `docker compose up` or `python -m scripts.dispatch_outbox` |

Plus PostgreSQL 16 (with pgvector) and Redis 7.

---

## 1. Pick your setup

| Setup | Use it when | Database | Auth | Extra work |
| --- | --- | --- | --- | --- |
| **A — Fully local** | Developing, trying the app, running the test suite. **Start here.** | Compose, or your own Postgres | Dev identity, no token | none |
| **B — With Supabase** | Real sign-in and real users | Same as A, or Supabase Postgres | Supabase-issued JWT | a Supabase project |
| **C — AWS** | Deploying somewhere real | Self-managed Postgres | same as B | a host, and optionally S3 |

All three share the same prerequisites, the same variables and the same
pipeline. **B and C change only who signs you in and where the files go** — the
API, worker, dispatcher and mobile app are identical. That is why this is one
document with three sections rather than three documents that drift apart.

Choose A, get it working end to end, then read B or C only if you need it.

---

## 2. Prerequisites

### 2.1 Required

| Tool | Why | Notes |
| --- | --- | --- |
| **Git + a Bash shell** | `tool/setup_mobile.sh` is a Bash script | Windows: use WSL2 or Git Bash |
| **Docker Engine + Compose v2** | easiest way to run all five services | check with `docker compose version` (verified: reports `v5.5.1`). A Docker-free path is in [§3.2](#32-run-it-without-docker) |
| **Flutter (stable)** | builds and runs the mobile app | Dart ≥ 3.4. Verified with Flutter 3.47.5 |
| **Android Studio** | Android emulator or device | add Xcode for iOS; macOS only |
| **Python 3.11 or 3.12** | only for the Docker-free path | the `Dockerfile` uses 3.11 |

If you skip Docker you also need **PostgreSQL 16 with the pgvector extension**
and **Redis 7**. The `pgvector/pgvector:pg16` image provides both.

### 2.2 Optional

| Thing | What it buys you |
| --- | --- |
| **An AI provider key** (Groq / Google Gemini / OpenAI) | Real model extraction and vector search. Without one, extraction falls back to a built-in heuristic and search falls back to keyword matching. Everything still works. |
| **Firecrawl key** | Better page fetching. Without one, Jina Reader and then the share-sheet preview text are used. |
| **S3 or R2 credentials** | Raw page snapshots. The backend only reads `S3_*` variables — no AWS CLI needed. `boto3` is already a dependency. |

### 2.3 Ports, and files you will generate

Ports **5432** (Postgres), **6379** (Redis) and **8000** (API) must be free.
If something else holds one, change the published port in `docker-compose.yml`
and the matching `DATABASE_URL` / `REDIS_URL`.

Not in a fresh clone, and not meant to be:

| Path | Made by |
| --- | --- |
| `.env` | you, from `.env.example` |
| `backend/.venv/` | your Python environment |
| `mobile/android/`, `mobile/ios/` | `tool/setup_mobile.sh` |
| `termination/` | a previous cleanup step; ignored by git, not part of the project |

---

## 3. Setup A — fully local

### 3.1 Run it with Docker

Copy the environment file and start everything:

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
docker compose up --build
```

Compose starts five services:

| Service | Image / command | Port |
| --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` — creates the `findback` database and loads `backend/init.sql` (the `vector` and `pgcrypto` extensions) | 5432 |
| `redis` | `redis:7-alpine` | 6379 |
| `api` | `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` | 8000 |
| `worker` | `celery -A app.celery_app.celery worker --loglevel=info` | — |
| `outbox` | `python -m scripts.dispatch_outbox` | — |

All three Python services mount `backend/`, so code edits are picked up without
a rebuild (the API reloads; restart the worker after editing it). All three read
your `.env`. Compose overrides `DATABASE_URL` and `REDIS_URL` with the
in-network addresses, so you do not edit those in `.env` for the Docker path.

**Schema.** With the default `SCHEMA_BOOTSTRAP=migrate`, the API applies every
Alembic migration itself on startup — there is nothing to do by hand. To apply
them yourself, or to see where the chain ends:

```bash
docker compose exec api alembic heads          # -> 0011_job_claim_time (head)
docker compose exec api alembic upgrade head
```

`docker compose ps` should show all five `Up`; `postgres` and `redis` should
additionally say `(healthy)`.

### 3.2 Run it without Docker

Only needed if you refuse Docker, or want the API native against a database you
already have. **All three Python processes must be started separately** — this
is the single most common way to end up with a save stuck.

**1. PostgreSQL with pgvector.** Reuse the same image:

```bash
docker run -d --name fbpg16 -p 5432:5432 \
  -e POSTGRES_USER=findback -e POSTGRES_PASSWORD=findback -e POSTGRES_DB=findback \
  pgvector/pgvector:pg16
```

Or a server you already have:

```bash
psql -U postgres -c "CREATE ROLE findback LOGIN PASSWORD 'findback' SUPERUSER;"
psql -U postgres -c "CREATE DATABASE findback OWNER findback;"
```

**2. Redis:**

```bash
docker run -d --name fbredis -p 6379:6379 redis:7-alpine
# or, natively:
redis-server
```

**3. Python environment:**

```bash
python3.12 -m venv backend/.venv
source backend/.venv/bin/activate          # Windows: backend\.venv\Scripts\activate
pip install -r backend/requirements.txt
```

**4. Three terminals, venv activated in each.** They read everything else from
`.env` in the repository root.

Terminal 1 — the API (this also runs the migrations):

```bash
cd backend
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — the worker:

```bash
cd backend
celery -A app.celery_app.celery worker --loglevel=info
```

Terminal 3 — the outbox dispatcher. Easy to forget, and its absence is
invisible until a save is stranded:

```bash
cd backend
python -m scripts.dispatch_outbox                  # loop, interval from .env
python -m scripts.dispatch_outbox --once           # one tick, then exit
python -m scripts.dispatch_outbox --interval 30    # override the interval
```

One tick prints what it did, e.g.
`{'claimed': 0, 'published': 0, 'failed': 0, 'items': 0, 'skipped': 0}`.

### 3.3 Run the mobile app

`mobile/android/` and `mobile/ios/` are **not in the repository** — they are
generated per machine. Once per machine, from the repository root:

```bash
tool/setup_mobile.sh
```

### 3.4 Check that it works

Every command below was run against this repository.

**1. The API and its dependencies answer:**

```bash
curl -s http://localhost:8000/health
```

```json
{"status": "ok", "service": "findback-api", "db": "ok", "redis": "ok",
 "queue": {"total": 2, "ready": 2, "failed": 0, "pending": 0, "processing": 0}}
```

`status` reflects the **database only**. `"redis":"down"` next to
`"status":"ok"` means the queue is unreachable: saves are still accepted, but
nothing is processed until it returns.

**2. Save a link:**

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://en.wikipedia.org/wiki/Mushroom_risotto","title_hint":"risotto"}'
```

Returns `{"id":"…","status":"processing","canonical_url":"…"}`. `processing`
means the save is committed and the worker has been handed it.

**3. Watch it reach `ready`:**

```bash
curl -s http://localhost:8000/api/v1/items?limit=5
```

```json
{"id": "…", "status": "ready", "summary": "…", "processed_at": "…"}
```

With no AI key configured the summary is the offline heuristic's rather than a
model-written one. That is expected; the **status** still has to reach `ready`.

**4. Search by meaning rather than by title:**

```bash
curl -s --get --data-urlencode 'q=creamy mushroom pasta' \
  http://localhost:8000/api/v1/search
```

**If a save does not move**, the database says exactly where it is stuck:

```bash
docker compose exec postgres psql -U findback -d findback \
  -c "SELECT status, attempt_count, last_stage, coalesce(last_error,'') FROM processing_jobs ORDER BY created_at DESC LIMIT 5;"
```

`last_stage` is the last pipeline stage that finished
(`FETCH → NORMALIZE → UNDERSTAND → BRIEF → CHUNK → EMBED`) and `last_error` holds
the reason for a failure. A healthy finished row reads `READY | 1 | EMBED |`
with an empty error.

**Optional checks.** Interactive API docs are at <http://localhost:8000/docs>.
The AI configuration can be probed from `backend/`:

```bash
python scripts/check_ai.py                  # resolved config + live chat ping
python scripts/check_ai.py --list-models    # model ids the provider offers now
python scripts/check_ai.py --embeddings     # embed a probe, report the vector width
```

Exit code `0` on success, so it can gate CI. With no keys it reports
`chat=off (heuristic), embed=off (keyword search)` and exits non-zero.

### 3.5 Run the test suite

Backend, unit only — no database needed:
---

## 4. Setup B — with Supabase

Setup A, plus real sign-in. Everything else — the pipeline, the queue, the app —
is unchanged.

**Not verified.** I have no Supabase project, so every step below is derived
from the code in `backend/app/auth.py` and
`backend/app/services/identity.py`, not from a run. What I *did* verify is the
behaviour the code implements, by driving it through the test suite.

### 4.1 How identity actually works here

This is the part worth reading carefully, because it is easy to assume the
backend identifies people by email.

**Identity is the token's `sub` claim**, written to `users.auth_subject`.
**Email is display only** — it labels the account in the UI and nothing more.

That split is deliberate. An email address can be reassigned by whoever buys it
next, so keying a library on one hands the previous owner's memories to the next
holder. The `sub` claim is the issuer's stable identifier for the person.

So `POST /api/v1/ingest` looks you up by `sub`, never by email. Changing your
email updates the label and moves nothing.

### 4.2 Supabase settings you need

| Setting | Where | Why |
| --- | --- | --- |
| **Project URL** | Project Settings → API | Public value; pass to the app as `--dart-define=SUPABASE_URL` |
| **JWT secret** | Project Settings → API → JWT Secret | The backend verifies every token against it with `SUPABASE_JWT_SECRET` |
| **Audience** | must match your tokens | `JWT_AUDIENCE` (default `authenticated`, which is Supabase's) |

Point the backend at them and turn dev auth **off**:

```bash
DEV_AUTH_ENABLED=false
SUPABASE_JWT_SECRET=<your project's JWT secret>
JWT_AUDIENCE=authenticated
```

`API_SECRET_KEY` is the fallback secret if `SUPABASE_JWT_SECRET` is unset —
useful locally, but use the real Supabase secret anywhere real.

Restart the API and worker after changing these; nothing reads them per request
except the audience and the claim path.

Send a token:

```bash
curl -s http://localhost:8000/api/v1/items \
  -H "Authorization: Bearer $TOKEN"
```

and give one to the app:

```bash
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000 \
            --dart-define=API_TOKEN="$TOKEN"
```

### 4.3 The database

You have two choices, and FindBack does not care which you take:

- **Keep the Compose Postgres** (Setup A). Simplest, and nothing else changes.
- **Use Supabase Postgres.** Enable the `vector` extension
  (`CREATE EXTENSION IF NOT EXISTS vector;` and `pgcrypto`), then set
  `DATABASE_URL` to the Supabase connection string. Apply the schema once with
  `alembic upgrade head` from a machine that can reach it.

**Not verified** — I have not run FindBack against Supabase Postgres.

### 4.4 Existing accounts get 401 until the verified-email claim is there

This is the one surprise worth reading before you try it.

When a user first signs in with a new token, the backend needs to decide whether
to trust that the person holding the token is the one who owned the existing
account with that address. It only does so if the token proves the address is
verified. **The gate fails closed**: no claim, no bind, HTTP 401 with
`"Cannot bind this account: the token does not prove the address is verified"`.

The claim is read from `AUTH_EMAIL_VERIFIED_CLAIM`, which defaults to
`app_metadata.email_verified`. It deliberately refuses to read
`user_metadata` — in Supabase a user can write their own `user_metadata`, so a
flag read from there proves nothing. The backend raises an error at startup if
you point it there.

So there are two ways out, and the second is the one that always works.

**Option 1 — a Custom Access Token Hook in Supabase (Supabase-managed).**
*Not verified — I have no project to test this against.* In Supabase, add a
Custom Access Token Hook that adds `app_metadata.email_verified: true` to the
token once the address is confirmed. The backend needs no change; it finds the
claim and binds on next login.

**Option 2 — bind the account by hand (always works).** For each affected
account, set `auth_subject` to the user's `sub`. Take the `sub` from their
token, or from Supabase → Authentication → Users.

```bash
docker compose exec postgres psql -U findback -d findback
```

```sql
---

## 5. Setup C — AWS

**Not verified.** I have no AWS account, so nothing in this section was run.
It describes what this repository actually ships, and marks the parts it does
not.

### 5.1 What the repository gives you, and what it does not

There is no Terraform, no CloudFormation, no ECS task definitions, no
CodeDeploy pipeline, no Secrets Manager integration and no deploy script in this
repository. The infrastructure is yours to stand up.

What *is* here is a container image (`backend/Dockerfile`), a Compose file that
runs all five services, and configuration that is entirely environment-driven —
no provider name, bucket name or host is compiled into the code. That means AWS
is mostly a matter of where you run those same containers.

| Piece | In this repo? | Notes |
| --- | --- | --- |
| Container image | **yes** | `backend/Dockerfile`, Python 3.11 |
| Five-service topology | **yes** | `docker-compose.yml` |
| All configuration via env vars | **yes** | nothing is hard-coded |
| S3/R2 snapshot storage | **yes, as env vars** | `S3_*`, via boto3 |
| Postgres with pgvector | image only | you run it; RDS is a *suggestion* below |
| ElastiCache / managed Redis | suggestion | nothing AWS-specific in the code |
| ECS / EKS / App Runner / load balancer / TLS termination | **no** | yours to build |
| Secrets Manager / Parameter Store integration | **no** | read env vars as normal |
| CI/CD | GitHub Actions only | `.github/workflows/ci.yml` runs tests; it does not deploy |

### 5.2 Running the stack on a host

**In the repo:** take `docker-compose.yml` to a host and change the two
`environment:` blocks so `DATABASE_URL` and `REDIS_URL` point at your managed
services instead of the in-network `postgres`/`redis` names. Everything else
already comes from `.env`, which Compose injects with `env_file: .env`.

```yaml
# suggestion — Compose as written, with the two overrides pointed outwards
api:
  environment:
    DATABASE_URL: postgresql://USER:PASSWORD@your-pg-host:5432/findback
    REDIS_URL: redis://your-redis-host:6379/0
```

Keep all five services. The worker and the dispatcher are separate processes for
a reason: the worker does the expensive work, and the dispatcher is what
recovers a save stranded by a queue outage. Scaling the API without scaling them
just queues more work.

**Suggestions, not repository features:** RDS or Aurora with the `pgvector`
extension enabled (a stock Postgres 16 with pgvector works equally well, and is
what I verified); ElastiCache for Redis; and a `.env` populated by whatever
secret mechanism you use, since Compose reads a plain file.

### 5.3 Production checklist

These come from `docs/OPERATIONS.md` and the code:

| Do this | Why |
| --- | --- |
| `DEV_AUTH_ENABLED=false` | otherwise anyone is the dev identity |
| A real `SUPABASE_JWT_SECRET` (or another real issuer) | tokens must actually be verified |
| `CORS_ORIGINS` narrowed to your app's origin | it defaults to a single localhost dev origin |
| Run **three** Python processes, not one | API, worker, dispatcher |
| TLS in front of the API | the Flutter app sends a bearer token on every call |
| Back up Postgres | it holds every user's memories |

### 5.4 S3 or R2 snapshots

Optional, and already in `requirements.txt` (`boto3`). The backend reads these
and nothing else — no AWS CLI, no profile discovery, no instance role needed:

| Variable | Meaning |
| --- | --- |
| `S3_BUCKET` | Bucket name. **If bucket, access key and secret key are not all set, snapshots are simply skipped** — nothing fails |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | credentials |
| `S3_ENDPOINT` | custom endpoint, for R2 or MinIO. Leave empty for real AWS S3 |
| `S3_REGION` | region name; `auto` is what Cloudflare R2 wants (the default in `.env.example`) |

-- 1. Find the unbound account (auth_subject is NULL).
SELECT id, email, auth_subject, auth_provider FROM users WHERE auth_subject IS NULL;

-- 2. Confirm the address really belongs to the person before you bind it.
--    Compare with the address in their Supabase profile.

-- 3. Bind it. Use the user's Supabase `sub` (the token's sub claim), not the id.
UPDATE users
   SET auth_subject = '<the-sub-claim>',
       auth_provider = 'supabase'
 WHERE email = 'user@example.com' AND auth_subject IS NULL;

-- 4. Confirm exactly one row changed.
SELECT id, email, auth_subject, auth_provider FROM users WHERE email = 'user@example.com';
---

## 6. Environment variables

`.env.example` is the annotated template and lists all of these. This section
groups them by which setup needs them. "Required" means the setup does not work
without it.

### 6.1 Core — all three setups

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `DATABASE_URL` | required | `postgresql://findback:findback@postgres:5432/findback` | Postgres connection string. Compose overrides it for you |
| `REDIS_URL` | required | `redis://redis:6379/0` | Celery broker. Compose overrides it for you |
| `DEV_AUTH_ENABLED` | A: yes · B/C: **must be `false`** | `true` in `.env.example` | `true` accepts requests with no token, as one dev identity. Never leave it on in production |
| `DEV_AUTH_EMAIL` | optional | `dev@findback.local` | Which identity dev auth acts as |
| `CORS_ORIGINS` | required in B/C | `http://localhost:8081` | Comma-separated allowed origins. Narrow it in production |
| `JOB_MAX_ATTEMPTS` | optional | `5` | Failed attempts before a processing job is parked `FAILED` |
| `OUTBOX_DISPATCH_INTERVAL_SECONDS` | optional | `5` | Seconds between dispatcher ticks |
| `SCHEMA_BOOTSTRAP` | optional | `migrate` | `migrate` applies Alembic on API startup; `create` builds from the ORM (throwaway DBs only); `none` leaves it to you |
| `RAW_TEXT_RETENTION_HOURS` | optional | `0` | How long fetched page text is kept. `0` drops it as soon as processing finishes |
| `TEST_DATABASE_URL` | tests only | unset | Postgres the live-DB tests create and drop throwaway databases in |

### 6.2 Auth — Setup B and C

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `SUPABASE_JWT_SECRET` | required | unset | Verifies Supabase access tokens. Falls back to `API_SECRET_KEY` |
| `JWT_AUDIENCE` | required in practice | `authenticated` | Must match the `aud` claim in your tokens |
| `API_SECRET_KEY` | required | `change-me-32-chars` | Fallback JWT secret when `SUPABASE_JWT_SECRET` is unset. **Change it** |
| `AUTH_EMAIL_VERIFIED_CLAIM` | optional | `app_metadata.email_verified` | Dotted path to the "address is verified" signal. Must not point into `user_metadata` — users can write that themselves, and the backend refuses at startup |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | app only | unset | Public values, passed to the app with `--dart-define`. Not secrets, and not read by the backend |

### 6.3 Mobile (`--dart-define`, not `.env`)

### 6.4 AI: keys

**Optional in all setups.** With no key at all the pipeline still runs: the
brief comes from a built-in heuristic and search falls back to keyword matching.
Verified — that is how this repository's own stack is currently configured.

| Variable | Meaning |
| --- | --- |
| `AI_PROVIDER` | `groq`, `gemini`, `openai` or `openai_compatible`. Empty auto-detects from whichever key is present, in the order Groq → Gemini → OpenAI |
| `GROQ_API_KEY` | Groq — chat only |
| `GEMINI_API_KEY` | Google AI Studio — chat **and** embeddings |
| `GOOGLE_API_KEY` | **The same key under a second name.** See below |
| `OPENAI_API_KEY` | OpenAI — chat and embeddings |
| `OPENAI_COMPATIBLE_API_KEY` | any OpenAI-compatible server (Ollama, vLLM, OpenRouter…) |
| `OPENAI_BASE_URL` | where that server is; default `https://api.openai.com/v1` |

**On `GEMINI_API_KEY` and `GOOGLE_API_KEY`.** They are not two different
credentials. They are **two spellings of one Google AI Studio key**, and both
serve the same `gemini` provider, for chat and for embeddings alike —
`GOOGLE_API_KEY` is never read for any other purpose anywhere in the codebase.
`GEMINI_API_KEY` is the documented name and is checked first;
`GOOGLE_API_KEY` is an accepted fallback for anyone whose existing `.env`
already used it. **Set one, not both.**

> The keys in `.env.example` are placeholders. If you keep the shipped
> placeholder text the backend treats it as unset rather than as a real key, and
> says so on startup.

### 6.5 AI: choosing a model

There are exactly **two** model roles, and one variable each. Anything left
empty falls back to the built-in default, which is what happened before these
variables existed.

| Variable | Role | Defaults |
| --- | --- | --- |
| `AI_MODEL` | the chat/text model: brief extraction, and the `check_ai.py` probe | Groq `llama-3.3-70b-versatile` · Gemini `gemini-3.8-flash` · OpenAI and compatible `gpt-4o-mini` |
| `EMBEDDING_MODEL` | the embedding model | Gemini `gemini-embedding-001` · OpenAI and compatible `text-embedding-3-small` · Groq `nomic-embed-text-v1_5` |
| `EXTRACTOR_MODEL` | the older name for the chat role. Still honoured, so an existing `.env` keeps its model. `AI_MODEL` wins if both are set | — |

To move to a different Flash version, set it in `.env`; there is no code change:

```bash
AI_MODEL=gemini-3.5-flash-preview
```

A model id the provider has retired is reported verbatim in the error, so the
fix is to set the variable to a current id.

> **Changing `EMBEDDING_MODEL` invalidates every vector already stored.**
>
> Search scores each item's stored vector against a freshly computed query
> vector. After a model change those two are in different spaces and nothing
> detects it: `items.embedding_model` records which model wrote each row, but
> `vector_search` and `chunk_search` do **not** filter on it, so old rows are
> silently scored alongside new ones and come back as meaningless neighbours.
> The one place that does compare `embedding_model` is the worker's content-reuse
> path, which reacts correctly — it declines to reuse and reprocesses.
>
> There is no re-embed tool yet, so treat `EMBEDDING_MODEL` as a
> choose-once-before-your-first-real-save setting: re-save your memories after a
### 6.6 Fetching, storage and search

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `FIRECRAWL_API_KEY` | optional | unset | Enables the Firecrawl step. The chain is YouTube transcript → Firecrawl → Jina Reader (free, no key) → the preview text you shared |
| `JINA_READER_ENABLED` | **no effect** | `true` | Listed in `.env.example` for historical reasons; nothing reads it |
| `S3_*` | optional | see §5.4 | Raw page snapshots. Unset simply skips the snapshot; nothing fails |
| `RRF_K` | optional | `60` | Rank-fusion constant for hybrid search |
| `VECTOR_WEIGHT`, `BM25_WEIGHT` | optional | `0.7`, `0.3` | Weight of vector vs keyword recall |
| `CHUNK_VECTOR_WEIGHT`, `CHUNK_LEXICAL_WEIGHT` | optional | `0.5`, `0.35` | Weight of a chunk hit within each list |
| `NOTE_LEXICAL_WEIGHT` | optional | `0.45` | Weight of the user's own note and intent |
| `RECENCY_WEIGHT` | optional | `0.001` | Tie-breaker only; keep it tiny |

---

## 7. Troubleshooting

The first five are the ones that actually bite, and all five were hit while
building this.

### The worker registers no tasks

**Symptom:** saves stay `pending`, and the worker log shows an empty task list.

```bash
docker compose logs worker | grep -A2 '\[tasks\]'
```

Healthy output names the task:

```
worker-1  | [tasks]
worker-1  |   . process_item
```

If `[tasks]` is followed by nothing, the worker imported `app.celery_app` but
not `app.tasks`. That module has to be listed in the Celery app's `include`,
which is what `app/celery_app.py` does. Usually it means an older checkout, or a
hand-typed worker command rather than
`celery -A app.celery_app.celery worker --loglevel=info`.

### The dispatcher is not running

**Symptom:** a save taken while Redis was down stays `pending` forever, and
nothing in any log mentions it. The publish failed, so no Celery message exists
anywhere; the *intent* is still a `PENDING` row in `processing_jobs`, and the
dispatcher is the only thing that ever republishes one.

```bash
docker compose ps                        # is `outbox` Up?
docker compose logs outbox               # a line per tick that claims work
docker compose exec outbox python -m scripts.dispatch_outbox --once
```

If `outbox` is missing from Compose it is an older checkout — that service was
added later. Without Docker, run `python -m scripts.dispatch_outbox` in its own
terminal (§3.2).

### Redis is down

**Symptom:** `/health` reports `"status":"ok"` **and** `"redis":"down"`. The API
is fine — `status` reflects the database only. Saves still return `200` with
`"status":"processing"`, and nothing processes them.

### 401 on `/api/v1/...`

**Symptom:** `401 Unauthorized` from any `/api/v1` route.

The backend is running with `DEV_AUTH_ENABLED=false`, which is correct for
Setup B and C. Send a token:

```bash
curl -s http://localhost:8000/api/v1/items -H "Authorization: Bearer $TOKEN"
```

If the token is valid and you still get 401, it is the legacy-account gate:
`"Cannot bind this account: the token does not prove the address is verified"`.
See [§4.4](#44-existing-accounts-get-401-until-the-verified-email-claim-is-there)
— either put the verified-email claim into your tokens, or bind the account by
hand.

### The backend suite reports a lot of skips

`295 passed, 288 skipped` is expected without a database. The skips are the
live-PostgreSQL tests. To run them:

```bash
cd backend && TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/findback python -m pytest -q
```

The role behind `TEST_DATABASE_URL` must be allowed to `CREATE DATABASE`, because
each test creates and drops a throwaway `fb_*` database.

### Other things people hit

| Symptom | Cause and fix |
| --- | --- |
| `env file .env not found` from `docker compose` | create it first: `cp .env.example .env` |
| `port is already allocated` for 5432 / 6379 / 8000 | something else holds it — stop it, or change the published port and the matching `DATABASE_URL` / `REDIS_URL` |
| Ingest works, search returns `[]` although items are `ready` | no embedding provider configured. Set an embedding-capable key, or accept keyword-only results |
| The Android emulator cannot reach the API | use `http://10.0.2.2:8000`, not `localhost` |
| A physical iOS device refuses plain HTTP | iOS blocks non-TLS except to localhost. Use the simulator, or put TLS in front |
| `Unable to locate asset ... android/` | `mobile/android/` has not been generated — run `tool/setup_mobile.sh` |
| Database says `expected 1536 dimensions` | `EMBEDDING_DIMS` does not match the `Vector(1536)` column — see `docs/OPERATIONS.md` |
| Flutter fails inside WSL with ``env: `bash\r` `` | the Flutter SDK was checked out with CRLF endings; re-check it out with `core.autocrlf=false` |

---

## Where else things are written down

| Document | Covers |
| --- | --- |
| `README.md` | what the project is, the API surface, repository layout |
| `PRODUCT.md` | the product rules the implementation must not break |
| `docs/OPERATIONS.md` | retention and deletion, log privacy, the AI gateway, embedding-width changes |
| `mobile/README.md` | client architecture and the offline-first contract |
| `docs/NATIVE_SHARE.md` | the remaining Android/iOS share work |
| `docs/ARCHITECTURE.md` | system design — **still describes the superseded Expo client**, and is being rewritten separately |

```bash
docker compose exec redis redis-cli ping        # PONG means it is alive
docker compose logs worker | tail -20
```

Bring Redis back and the queued work drains on its own. Anything the fast path
failed to publish is picked up by the dispatcher within
`OUTBOX_DISPATCH_INTERVAL_SECONDS`.

### Saves stay `pending` or `processing` and never reach `ready`

Ask the database rather than guessing — `last_stage` and `last_error` say
exactly where it stopped:

```bash
docker compose exec postgres psql -U findback -d findback \
  -c "SELECT status, attempt_count, last_stage, coalesce(last_error,'') FROM processing_jobs ORDER BY created_at DESC LIMIT 5;"
```

| What you see | Meaning |
| --- | --- |
| `PENDING \| 0 \|` (no stage yet) | never handed to a worker — check the worker and the dispatcher |
| `PROCESSING \| … \|` with a growing `attempt_count` | the worker is failing and retrying; read `last_error` |
| `FAILED` once `attempt_count` reaches `JOB_MAX_ATTEMPTS` | gave up; the item is `failed` and the app shows a failed state rather than spinning |
| `READY \| 1 \| EMBED \|` | healthy — a finished save |

One case worth knowing: a save can reach `READY` and still show a thin summary,
because the fetcher falls back to `Link: <url>` when no page fetch succeeds.
That is a fetching problem, not a pipeline one — `last_stage` will say `EMBED`
and the summary will look like a link.

> change, or leave it alone. Changing `EMBEDDING_DIMS` fails more safely — a
> wrong-width vector is dropped before it reaches the database, so new items
> simply go unvectorised and search leans on keyword recall.

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMBEDDING_PROVIDER` | unset | Unset reuses the chat provider when it can embed. Groq's model is ~137-dimensional and cannot fill the 1536-wide column, so a Groq-only setup should point this at Gemini or OpenAI, or accept keyword-only search |
| `EMBEDDING_DIMS` | `1536` | Must match `Vector(1536)` in the schema |
| `EMBEDDING_BATCH_SIZE` | `96` | Contents per embedding request; Gemini's ceiling is 100 |
| `EMBEDDING_NORMALIZE` | `true` | L2-normalise truncated Gemini vectors, as Google requires |
| `AI_TIMEOUT_SECONDS` | `45` | Per-request HTTP timeout |
| `AI_MAX_RETRIES` | `3` | Total attempts (1 + retries). Timeouts, 429 and 5xx are retried; other 4xx are not |
| `AI_USE_JSON_MODE` | `true` | Ask for a JSON object; auto-dropped if a provider refuses |
| `EXTRACTOR_MAX_TOKENS` | `900` | Cap on the extraction completion |
| `EXTRACTOR_MAX_CHARS` | `8000` | Page characters sent for extraction |
| `EMBED_MAX_CHARS` | `8000` | Characters per embedded string |
| `AI_CONCURRENCY`, `EMBEDDING_CONCURRENCY`, `FETCH_CONCURRENCY` | `4`, `2`, `4` | Simultaneous outbound calls per stage |
| `AI_RATE_PER_SEC`, `AI_BURST` | `5`, `10` | Sustained calls per second, and the burst above it |
| `AI_DEBUG` | `false` | Logs full provider request/response bodies — they contain saved page text, so leave it `false` |

A compiled Flutter binary reads **no `.env` file** — configuration is compiled
in from `mobile/lib/config.dart`.

| Define | Default | Meaning |
| --- | --- | --- |
| `API_BASE_URL` | `http://localhost:8000` | API root, no trailing slash. Use `http://10.0.2.2:8000` from an Android emulator |
| `API_TOKEN` | unset | Seeds the token store, for a backend with `DEV_AUTH_ENABLED=false` |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY` | unset | Public Supabase values for the sign-in flow, which is not built yet |

Only public values belong here. Never bake a secret into a binary.

```

If step 3 says `UPDATE 0`, someone already bound it, or the address is not the
one you think — stop and check rather than binding the wrong row. The
`auth_subject` column is unique, so two accounts cannot end up on one subject.


```bash
cd backend && python -m pytest -q
# 295 passed, 288 skipped
```

The skips are the tests that need a real PostgreSQL. To run those too, point
them at a database whose role may `CREATE DATABASE` (they create and drop
throwaway `fb_*` databases):

```bash
cd backend && TEST_DATABASE_URL=postgresql://findback:findback@localhost:5432/findback python -m pytest -q
# 583 passed
```

Mobile and the offline search gate:

```bash
cd mobile && flutter analyze && flutter test    # clean, 67 tests
python eval/eval_offline.py                      # Recall@5=1.000 MRR=1.000 queries=5
```

All four of those numbers above are the ones this repository produced on
2026-10-03, in the Docker setup described above.

It generates the platform projects in a temporary directory (so your hand-written
`lib/` and `pubspec.yaml` are never overwritten by templates), copies only
`android/` and `ios/` into `mobile/`, then runs `flutter pub get`, `flutter
analyze` and `flutter test`. Re-running is safe. Options:
`FINDBACK_ORG_ID=com.example` for a different bundle id,
`FINDBACK_EXTRA_PLATFORMS=linux` to also generate desktop. On Windows run it
from WSL or Git Bash.

Then:

```bash
cd mobile
flutter pub get

# Android emulator — 10.0.2.2 is how an emulator reaches the host's localhost
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000

# iOS simulator on macOS — localhost already points at the host
flutter run --dart-define=API_BASE_URL=http://localhost:8000

# a physical device on the same Wi-Fi — use your machine's LAN IP
flutter run --dart-define=API_BASE_URL=http://192.168.1.50:8000
```

`flutter devices` lists targets; add `-d <id>` when more than one is connected.
`r` hot-reloads, `R` restarts, `q` quits. Release builds:

```bash
flutter build apk --release --dart-define=API_BASE_URL=https://api.example.com
flutter build appbundle --release --dart-define=API_BASE_URL=https://api.example.com
flutter build ipa --release --dart-define=API_BASE_URL=https://api.example.com   # macOS + signing team
```
