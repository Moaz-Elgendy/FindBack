# Public testing audit — 2026-10-08

This was a read-only product/security investigation. No application code,
production records, migrations, or deployment configuration were changed.
It is not a penetration-test certification or a claim that all vulnerabilities
have been found.

## TikTok analysis

The active EC2 API and configured Supabase database both reported migration
`0013_job_attempt_token`, with three ready Facebook items. Neither contained a
TikTok record. Completed guest memories are cached locally and removed from
server staging by `GuestLibrary.cacheAndRelease`.

The phone's guest cache contained a completed memory for
`https://www.tt.site/t/ZSbb5dNN9/`, item
`51da8aa9-782b-4e8e-86cb-be69c5efc1aa`. Its instant brief describes generic TikTok
navigation links and unavailable content, with no key points. Its payload has
`brief_source="llm"`, `needs_retry=false`, and status `ready`. This is a real LLM
brief with inadequate evidence, rather than a provider-failure fallback.

The user supplied a different link, `https://www.tt.site/t/ZSbbndnpn/`.
An HTTP redirect check resolved it to a TikTok video URL, but that exact saved
record was not found in the inspected cache/database.

`fetcher.video_source` recognizes tiktok.com but returns `None` for tt.site.
`pipeline.stage_fetch` only calls media acquisition for recognized video URLs.
Consequently these aliases bypass video download, OCR, and STT. The retry policy
also depends on the original URL's video classification. This explains the
observed navigation-only final brief. No new video processing or real-LLM run
was performed in this audit.

## Launch concerns

1. **Unbounded processing intake:** the public guest-token endpoint and authenticated
   ingest routes have no application intake quota. The existing AI limits are
   process-local; they do not enforce shared account-wide provider quotas or a
   daily spend cap. Guest/account creation and processing can exhaust resources.
2. **Persistent fallback retries:** `brief_retry.should_retry` always returns true
   for a fallback. Backoff caps at one hour; it is not a total attempt/spend cap.
   The user requirement to retry must be implemented within a shared budget,
   with work retained while provider capacity is unavailable.
3. **Dependency advisories:** both requirements and installed-environment audits
   reported 39 advisory entries across six packages, representing 20 distinct
   advisory IDs. Affected versions: python-jose 3.3.0, python-multipart 0.0.9,
   Starlette 0.36.3, lxml 5.1.0, ecdsa 0.19.2, pytest 8.0.0. Entries include
   duplicates; applicability requires review. Some fixes need compatible
   FastAPI/Starlette upgrades. The running EC2 API also reports those six installed versions. These are not
   39 demonstrated application exploits.
4. **URL safety gap:** request validation accepts loopback and instance-metadata
   URLs; no public-address guard was found in the reviewed intake/media path.
   This acceptance was checked without making internal-network requests. It does
   not by itself prove an EC2 SSRF exploit. Public URL, redirect, DNS, and media
   destination checks are required before broader video support.
5. **Public operational metrics:** `/health` and `/metrics` expose aggregate queue
   and operational information without authentication. No private memory content
   was observed in the health response.
6. **Previously disclosed credentials:** the Supabase secret and 21st API key
   previously pasted into chat should be rotated. Neither belongs in a mobile
   build. The reviewed mobile configuration uses the Supabase publishable key.

## Positive checks and limits

- Core tables `items`, `users`, `content_assets`, `user_memories`,
  `processing_jobs`, and `chunks` have RLS enabled. Both `anon` and
  `authenticated` have no direct SELECT or INSERT grants on these tables.
- Reviewed item access/delete routes scope records to the authenticated user;
  ingest SQL uses bound parameters. This is not an exhaustive injection audit.
- Mobile access tokens use Flutter secure storage.
- Backend dangerous-pattern scan reported no findings. That scanner does not
  establish overall security; its mobile UX counterpart also reports icon/text
  sizes as touch targets and gives React-specific advice for Flutter. Those
  results were not treated as confirmed defects.
- The phone was on its system screen during UI capture; no new visual
  acceptance test was performed. No unrelated phone content was inspected.

## Checks run

From `backend`, against the dedicated local test database:

```bash
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 .venv/bin/python -m pytest -q tests/test_auth_http.py tests/test_phase4_privacy.py tests/test_phase7_accounts.py tests/test_phase7_limits.py tests/test_brief_integrity.py tests/test_brief_retry.py tests/test_media_understanding.py
```

Result: **57 passed, 24 warnings in 28.89s**. No tests ran against production.

From the repository root:

```bash
uvx --python 3.11 pip-audit -r backend/requirements.txt --format json --output /tmp/findback-security-dependency-audit.json
uvx --python 3.11 pip-audit --path backend/.venv/lib/python3.12/site-packages --format json --output /tmp/findback-security-installed-audit.json
python .agents/skills/vulnerability-scanner/scripts/security_scan.py backend/app --scan-type patterns
python .agents/skills/mobile-design/scripts/mobile_audit.py mobile
python .agents/skills/ui-ux-pro-max/scripts/search.py 'personal knowledge saved video memories collections simple mobile' --design-system -p FindBack
curl --head --location --max-redirs 5 --max-time 20 --silent --show-error https://www.tt.site/t/ZSbbndnpn/
```

Dependency audits exited 1 because advisories were found. Database inspection
used read-only sessions; phone SQLite inspection used a private temporary copy.
Secrets and other users' identities were not printed. No dependency upgrades,
media repairs, UI changes, or new collections schema have been implemented yet.
