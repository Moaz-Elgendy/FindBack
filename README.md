# FindBack

Save links now. Find them later using what you remember.

FindBack is a Flutter app backed by FastAPI, PostgreSQL 16 + pgvector, Redis 7,
and Celery. Saves enter a durable SQLite queue before network access; workers
fetch content, extract a structured Brief, and build hybrid semantic/keyword
search indexes. Optional Supabase accounts keep libraries isolated. Guests keep
completed memories on their device and use temporary server processing.

## Features

- Offline capture, multi-link Android sharing, automatic sync and cached reads.
- Search by meaning, topic, type, entity, intent, source, or saved date.
- Evidence-grounded Briefs with video timestamps, bilingual transcription/OCR,
  and bounded provider retries. Unavailable evidence is shown honestly.
- Private collections, editable memories, explicit re-summarizing, deletion/Undo,
  and device reminders. Failed re-summarizing preserves the previous Brief.
- Optional accounts, JSON export, account deletion, and an opt-in weekly note
  linking to a frozen “Worth another look” list.
- Light/dark/system appearance, Arabic font fallback, RTL and large-text support.

## Run locally

Requires Docker Compose and Flutter **3.47.6** (the CI version). A native Python
setup also needs Python 3.11+, ffmpeg, Tesseract with English/Arabic data, and the
packages in `backend/requirements.txt`. CPU transcription downloads its model
on first use.

From the repository root:

```bash
cp .env.example .env              # Only on first setup; preserve an existing .env.
# Set a strong API_SECRET_KEY and your chosen AI credentials in .env.
docker compose up -d postgres redis
# Back up existing databases first. The one-shot migrate service gates startup.
docker compose up -d --build api worker outbox beat
curl --fail http://localhost:8000/ready

tool/setup_mobile.sh              # Generates native scaffolding and runs checks.
cd mobile
flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000
```

Ordinary Flutter builds default to `https://findback.duckdns.org`; `.env` does not
configure a compiled app. Use `API_BASE_URL` explicitly for local development.
PostgreSQL is published at `127.0.0.1:55433`; Redis uses port 6379 and the API
uses port 8000. Android emulators reach the host through `10.0.2.2`; iOS simulators
use `localhost`. For a physical Android device, `tool/run_android.sh [device-id]`
checks processing readiness and maintains ADB forwarding. Android native share intake is implemented; an iOS
Share Extension is still required.

For a backend without Docker, install the requirements into `backend/.venv`,
set `DATABASE_URL` to the host PostgreSQL URL and `REDIS_URL` to the host Redis,
then run Alembic, Uvicorn, Celery worker, Celery beat, and
`python -m scripts.dispatch_outbox` from `backend`. All four processes are needed
for automatic processing and weekly scheduling. Run exactly **one beat**.

`/ready` returns 503 unless the API, database, schema revision, Redis, worker,
dispatcher and beat are ready. `/health` includes the same diagnostics but remains
a 200 response for inspection. Processor heartbeats expire after 60 seconds.
An unversioned existing database needs a backed-up schema comparison and controlled
adoption before migration; never stamp `head` to hide missing migrations. The app
retains failed uploads, distinguishes backend failures from offline captures, and
honors guest-session and upload `Retry-After` delays.

## Configuration

`.env.example` is the configuration reference; `.env` and credentials stay local.

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL`, `REDIS_URL`, `API_SECRET_KEY` | Database, queue and guest-token signing |
| `DEV_AUTH_ENABLED` | Local development identity only; disable publicly |
| `AI_PROVIDER`, `AI_MODEL`, provider keys | Groq, Gemini, OpenAI or compatible chat |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Separate embeddings; stored vectors are 1536-wide |
| `SUPABASE_URL`, `SUPABASE_JWT_SECRET` | Account issuer/JWKS; legacy local HS256 configuration |
| `SUPABASE_SERVICE_ROLE_KEY` | Backend-only authentication identity deletion |
| `CAPACITY_LIMITS_ENABLED`, provider budgets | Shared Redis quotas; unavailable enforcement pauses work |
| `MEDIA_*`, `STT_*`, `VISION_AI_*` | Video download, speech, OCR and optional frame understanding |
| `PUBLIC_CACHE_TTL_DAYS` | Sliding lifetime of verified anonymous results (30 days); personal copies are retained |
| `PUBLIC_CACHE_CLEANUP_SECONDS`, `PUBLIC_CACHE_CLEANUP_BATCH` | Hourly bounded cleanup of expired global payloads |
| `SUMMARIZE_AGAIN_DAILY_LIMIT` | Per-memory UTC daily regeneration quota (3); previous brief survives failure |
| `FCM_SERVICE_ACCOUNT_FILE`, `FCM_PROJECT_ID` | Optional server-side weekly push credentials/project |
| `TARGET_DATABASE_URL`, `API_DOMAIN`, `BACKEND_IMAGE` | Production database, HTTPS domain and immutable image |

No provider credentials means limited evidence/fallback Briefs and lexical search;
it does not establish successful AI processing. Provider failures retain retryable
work. Changing the embedding model/width requires a deliberate compatible data
migration/re-embedding plan. Private/unknown content is never deduplicated across
owners; user notes and edits remain private. Raw fetched text is discarded by
default after processing; summaries, chunks and vectors remain while saved.

Normal builds include the public production API and Supabase settings. The app
does not read `.env`; use build defines to override those settings:

```bash
flutter run --dart-define=API_BASE_URL=https://findback.duckdns.org \
  --dart-define=SUPABASE_URL=https://YOUR_PROJECT.supabase.co \
  --dart-define=SUPABASE_PUBLISHABLE_KEY=YOUR_PUBLIC_KEY
```

Configure Supabase email verification and recovery redirect
`findback://auth/recovery`. Never put a service-role key, private key or provider
credential into a mobile build. Account export requires an online server read;
provider deletion failure preserves the application account and its saves.

For weekly push, provide ignored `mobile/android/app/google-services.json`; its
Gradle plugin is applied only when that file exists. On iOS, add the ignored
`GoogleService-Info.plist` to the Runner target, enable Push Notifications and
Remote notifications, and configure APNs in Firebase. Mount a service-account
JSON **inside the worker container** at `FCM_SERVICE_ACCOUNT_FILE`; an absolute
host path alone is insufficient. Without this setup, push is unavailable and the
rest of the app continues working. Local reminders use native scheduling and
resume reconciliation; OS policies can delay delivery.

## API

Interactive contracts: `/docs` and `/openapi.json`. All `/api/v1` routes except
guest-token issuance require bearer authentication, unless local dev auth is on.

| Routes | Purpose |
| --- | --- |
| `POST /auth/guest`, `POST /ingest`, `POST /sync/batch` | Guest identity, capture, queued uploads |
| `GET /search`, `GET /items`, `GET /items/{id}` | Search, paginated library, detail |
| `PATCH /items/{id}`, item retry/keep-link/summarize-again | Private edits and explicit processing actions |
| Item delete/restore and reminder routes | Memory lifecycle and absolute UTC reminders with IANA zones |
| `/memories/{content_id}`, `/collections` | Owner notes/intents and collection membership |
| `/account/weekly-note`, `/account/export`, `DELETE /account` | Preferences, JSON export, permanent account deletion |
| `POST /items/{id}/open`, `/devices`, `GET /snapshots/{id}` | Open tracking, push registration, frozen weekly lists |

Paths in this table use the `/api/v1` prefix. `/health` reports database/Redis
readiness; `/metrics` exposes operational metrics. Snapshots and item reads are
owner-scoped; another account's identifiers return 404. Weekly notifications
contain a count and generic text, never memory titles.

## Checks

Use a dedicated test database; the suite refuses production-style database names.
Its PostgreSQL role needs `CREATE DATABASE` because integration fixtures create
and drop disposable databases. With local Compose services running:

```bash
# Once, if this test database does not already exist:
docker compose exec postgres createdb -U findback findback_test

python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
TEST_DATABASE_URL=postgresql://findback:findback@127.0.0.1:55433/findback_test \
  REDIS_URL=redis://127.0.0.1:6379/15 \
  backend/.venv/bin/python -m pytest -q backend/tests
(cd mobile && flutter pub get && flutter analyze && flutter test --reporter expanded)
python3 infrastructure/test_deploy.py
python3 tool/tests/test_run_android.py
python3 eval/eval_offline.py
backend/.venv/bin/python scripts/eval_brief.py
```

`eval/eval_offline.py` is a small lexical fixture gate, not production semantic
search quality. Backend evaluation/load tests measure the real pipeline with
external boundaries mocked. Live provider evaluation is explicitly opt-in;
`scripts/eval_search_brief.py` seeds database records and must use a disposable
database. Keep test output and generated artifacts outside Git.

## Repository and deployment

`mobile/lib` contains the app; `backend/app` contains the API/workers;
`backend/alembic` contains schema history through `0025_snapshot_reconciliation`.
`backend/tests` and `mobile/test` cover behavior. `tool` contains mobile launchers;
`scripts`, `eval` and `backend/evals` contain evaluation tools;
`infrastructure` contains Terraform, production Compose, Caddy and deployment checks.

The existing hosted endpoint is `https://findback.duckdns.org`. Production uses
EC2 in `eu-west-1`, Supabase PostgreSQL/auth, private Redis, and Caddy HTTPS.
CI tests pushes/PRs; successful `master` pushes or manual runs build an immutable
ECR image and deploy through AWS OIDC/SSM. Repository variables:
`AWS_REGION`, `ECR_REPOSITORY_URL`, `EC2_INSTANCE_ID`, `AWS_DEPLOY_ROLE_ARN`.
`infrastructure/deploy.sh` serializes updates and rolls back image/config on failed
readiness. A one-shot migration service runs before API/processing startup; back up
before schema changes. Image/config rollback does not downgrade the database, so
migrations must remain compatible with the previous release. Never use
Terraform teardown as application rollback. Processing heartbeats are scoped to each
deployment generation so a previous worker cannot approve a new release. Rollback
starts the previous services without rerunning its migrator, verifies health, and
records the database’s applied revision for readiness; migrations must remain
compatible with the preceding application image. Retain private Terraform state and
backups outside Git; commit dependency lockfiles and handwritten native sources.

Project history, phase status, and remaining validation are consolidated in
[docs/PHASES.md](docs/PHASES.md). Private project; all rights reserved.

Migration `0025` reconciles snapshot privacy and preserves original counts for
databases that already applied an earlier `0021`. After backing up the database,
run `alembic upgrade head` before deploying. Previously lost snapshot IDs cannot
be recovered; downgrading `0025` deliberately retains its privacy/count fixes.

## Snapshot sharing

Share a ready memory from its detail dock; the sheet also offers the original
source URL. Sign in to create or redeem a memory link. Account → Sharing stores an
optional public display name and lists active links with Revoke. No sender email
is included. Link expiry, revocation and sender deletion never remove copies a
recipient already saved. Source deletion leaves its explicit snapshot available.

`SHARE_LINK_TTL_DAYS` is the single lifetime setting (30 by default). Configure
`SHARE_LINK_ORIGIN`, `ANDROID_APP_LINK_PACKAGE`, and the installed build's actual
`ANDROID_APP_LINK_FINGERPRINTS` before verifying Android App Links. The origin must
match the compiled API origin and Manifest host. Set `ANDROID_PLAY_STORE_URL` only
when a real listing exists; otherwise the fallback omits installation links.
Native iOS association requires an Apple signing team; none is configured here.

Migration `0027_memory_sharing` is additive. Recipient attribution stays in the
existing SQLite brief payload; pending links use encrypted, origin-scoped device
storage across sign-in. Snapshots copy only display content and a lexical index,
with no private tags, hints, raw extraction, embeddings or AI processing at
redemption. See [Phase 5 evidence](docs/phase5-verification.md).


## Verification and rollout

Back up PostgreSQL before deploying; startup applies additive migrations through
`0027_memory_sharing`. SQLite upgrades automatically to version 8 and preserves
saved copies. Keep API, worker, dispatcher and beat running through the provided
deployment configuration. During processing, Library also checks `/ready` on its
existing refresh cadence and shows an outage without hiding saved cards.

Compile only public mobile settings (`API_BASE_URL`, `SUPABASE_URL`,
`SUPABASE_PUBLISHABLE_KEY`); the Flutter binary does not read `.env` at runtime.
Never pass the server's complete `.env` as mobile build definitions. Credential
URLs stay owner-scoped and cannot enter the public cache or snapshot links.

See [Phase 6 evidence](docs/phase6-verification.md) and the
[unchecked device checklist](docs/manual-device-checklist.md) before rollout.
Production deployment runs from `master` after both test jobs pass. AWS OIDC
trust must match GitHub’s exact immutable subject prefix (owner/repository IDs)
and the deployment branch; Terraform keeps this in `github_oidc_subject_prefix`.
Account deletion requires the server-only `SUPABASE_SERVICE_ROLE_KEY`; configure
it securely on the server, never in Flutter build definitions.
