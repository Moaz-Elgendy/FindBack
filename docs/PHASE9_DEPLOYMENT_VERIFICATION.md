# EC2 and Supabase application deployment

Verified October 7, 2026. The public API is **https://findback.duckdns.org** on EC2 `i-0eb2c28f2a9070163`, Elastic IP `52.210.150.132`, region `eu-west-1`. Authentication and application PostgreSQL are hosted by the existing Supabase project; its Session pooler is in `eu-central-1`.

## Database migration proof

Local API, worker, and outbox services were stopped before the snapshot. Local PostgreSQL and Redis remain available; the original database was not deleted or repointed. The ignored local `.env` retains its original `DATABASE_URL`; `TARGET_DATABASE_URL` is used by production Compose.

`infrastructure/migrate_database.py` refused nonempty targets, backed up both public schemas, and restored the source public schema in a single transaction. Supabase's auth schema was outside the dump and restore. The existing pgcrypto extension remained in `extensions`; vector was installed in `public` for compatibility with the copied schema.

Before production workers and smoke tests ran, row counts and SHA-256 fingerprints over every complete row matched in every table:

| Table | Rows |
| --- | ---: |
| alembic_version | 1 |
| chunks | 4 |
| content_assets | 4 |
| items | 3 |
| processing_jobs | 7 |
| user_memories | 3 |
| users | 4 |

Migration revision: **0013_job_attempt_token**. The three original saved memory IDs were subsequently checked and all remained **ready**. New guest sessions/jobs created during verification naturally change later counts; the table comparison above describes the migration snapshot.

All seven public application tables have RLS enabled, with direct table access revoked from Supabase `anon` and `authenticated` roles. Default table grants for the restoring role were restricted before table creation in the same transaction. SQL privilege checks confirmed neither client role had SELECT on these tables; Supabase REST rejected an anonymous direct items request with 401. The authenticated backend connects as the database owner and enforces per-user access through existing API code; the mobile app does not query these tables directly.

Backups are private local files at `~/.local/state/findback/backups/phase9-deployment/`: `local.dump`, `supabase-before.dump`, and restore logs/list. Do not commit backups or credentials. Do not rerun the migration script against this populated target; its empty-target guard will refuse.

## Production configuration

`infrastructure/compose.production.yml` runs the existing API, one Celery worker, outbox dispatcher, private persistent Redis, and Caddy. Only ports 80/443 are published. API port 8000 and Redis port 6379 have no host bindings. No live source directory is mounted. The API runs without development reload, shared development authentication is disabled, and schema bootstrap is manual. Redis data, certificate data, and downloaded model cache use persistent volumes.

Deployment is installed at `/opt/findback` on EC2. The private environment file there is mode 0600 and excludes the Supabase secret/service-role key, legacy JWT secret, test database credentials, and VM/AWS metadata. PostgreSQL uses the configured Session-pooler URL with `sslmode=require`.

Observed remote checks:

- API and Redis containers healthy; Caddy, worker, and outbox running.
- HTTPS health: status **ok**, database **ok**, Redis **ok**.
- HTTP health request: **308** redirect; HTTPS request: **200**, with normal certificate verification.
- Anonymous API library request: **401**.
- Two guest sessions initially had empty libraries. One guest's saved item was **404** to the other guest.
- A real `https://docs.python.org/3/tutorial/venv.html` save reached **ready**, with `brief_source=llm` and a nonempty stored instant Brief. Owner search for `python virtual environments pip` returned it; the second guest's search was empty. The test memory was deleted afterward.
- Worker logs recorded job completion through BRIEF, CHUNK, EMBED, and READY. This was a text-article test; it does not establish video transcription/OCR behavior on EC2.

## Commands actually run

From the repository:

```bash
python infrastructure/verify_deployment.py
docker compose stop api worker outbox
backend/.venv/bin/python infrastructure/migrate_database.py
backend/.venv/bin/python ~/.local/state/findback/ec2/smoke.py
git -c core.whitespace=cr-at-eol diff --check
```

The configuration assertion check passed before deployment and again afterward. The migration exited 0 with all table counts/fingerprints matching. The live smoke script exited 0. Migration security checks and original-record checks passed. No pytest commands were run against the production database.

On EC2, from `/opt/findback` (restart the configured image; new releases use the [CI/CD deployment](CICD_VERIFICATION.md)):

```bash
docker compose --env-file .env -f infrastructure/compose.production.yml up -d --no-build
```

A debug APK was built with the hosted `API_BASE_URL` and only the public Supabase URL/publishable key. `adb install -r mobile/build/app/outputs/flutter-apk/app-debug.apk` succeeded, then the app was restarted and `adb reverse --remove tcp:8000` removed the local API tunnel. After the user unlocked the phone, UI inspection showed the library, account icon, and three visible cached-memory cards, with no login gate or connection warning. No ADB reverse forwarding remains. The APK is configured for the hosted HTTPS URL. Public connection probes also confirmed ports 5432, 6379, and 8000 inaccessible.

## Limits and rollback

Real user signup/login/password-reset delivery and second-device account synchronization were not tested: no user credentials were provided. Mobile-data-only access and live Facebook media acquisition/STT on EC2 were not tested. The article processing/search and TLS tests above were real hosted requests.

The old local API/worker/outbox remain stopped to avoid operating two copies of the library. Leave them stopped during hosted use. To roll back application access, first back up any new hosted records, then start the preserved local services and rebuild the app for the local endpoint. New hosted saves are not automatically mirrored into the old local database. Do not destroy EC2 or overwrite the hosted database as a rollback shortcut.

For hosted restarts, SSH to EC2, change to `/opt/findback`, and use the production Compose file shown above. Credentials and Terraform state remain ignored/private. The existing free-plan/credit limits still apply to EC2, storage, and the Elastic IP.
