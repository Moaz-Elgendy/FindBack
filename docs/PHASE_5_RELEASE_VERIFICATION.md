# Phase 5 release verification — 2026-10-08

## Deployed release

EC2 in `eu-west-1` serves `https://findback.duckdns.org` using ECR image
`findback-backend:phase5-20261008070956`, digest
`sha256:5e8fce621016a7d3eaab77284d6c5caa97b145602c9c1f086a2f6365a631185d`.
API and Redis health checks pass; worker and outbox containers are running.
The final aggregate health response still listed two pending and one failed job
outside the disposable smoke run; their causes were not investigated in this phase.
API/Redis ports are private; public ingress is HTTPS/HTTP, with restricted SSH.
IMDSv2 is required. No Git commit or push was performed.

Before migration, a PostgreSQL custom-format public-schema backup was stored at
`/opt/findback/backups/phase5-20261008070956/supabase-public.before.dump`.
Its archive listing was verified; a restore drill was NOT RUN.
Supabase now reports `0014_collections`. Both collection tables have RLS enabled.
Full-row fingerprints confirmed the three original saved items were unchanged.

Shared capacity enforcement is enabled. The configured Gemini daily request cap
is 20, matching the quota observed during evaluation. This bounds calls; it does
not provide unlimited free inference.

## Changes verified in this phase

- TikTok reader/navigation pages are excluded from video evidence acquisition;
  unrelated TikTok Shop recommendations cannot become the video's brief.
- Evaluation acquisition uses the resolved URL, as production does.
- Migration-test fixtures remove the new collection tables/constraint before
  replaying older migrations and expect the current migration head.
- Auth regressions reject a DER-public-key HS256 token and verify the ES256
  cryptography backend.

## Automated checks

From the repository root:

```bash
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -q backend/tests
```

Result: **826 passed, 1 skipped in 280.12s**. Production was not used for pytest.

Resumed focused verification:

```bash
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -q backend/tests/test_auth_advisory_guards.py backend/tests/test_video_aliases.py backend/tests/test_collections.py
python infrastructure/test_deploy.py
python infrastructure/verify_deployment.py
```

Results: **15 passed in 3.34s**; **2 deployment cases passed**, including readiness
failure rollback; deployment configuration verification passed.

From `mobile`: `flutter test` reported **158 passed, 1 skipped**;
`flutter analyze` reported **No issues found**.
The hosted debug APK built and installed successfully, preserving phone data.
Library styling, saved dates, account/status area and Library/Collections tabs
were visually checked. Collections interactions on the phone were NOT verified:
the phone switched to its browser during the check.

## Hosted integration evidence

An isolated two-guest smoke test verified unauthenticated library access returns
401, cross-user memory access returns 404, and collection access/update is scoped
to the owner. Temporary test collections and memories are removed by the test;
original saved memories are not deleted.

The normal queue path generated a real `brief_v3.4` brief for the Python pathlib
documentation. Gemini returned three 503 responses and automatically failed over
to Groq `openai/gpt-oss-120b`. The stored brief used `brief_source="llm"` and
`needs_retry=false`. Embedding then encountered the shared Gemini cooldown and
was scheduled for an automatic retry. The dispatcher resumed at EMBED after the
cooldown; Gemini embedding returned 200 and the item reached **READY** with the
real LLM brief and `needs_retry=false`. The smoke test completed successfully and
removed its temporary memory and collection. No manual job mutation or
process-local retry was used. Live search ranking was NOT tested in this smoke.

## Limits and security findings

The supplied TikTok resolves and downloads as a five-second video. Visual analysis
was blocked by Gemini quota exhaustion; an accurate visual brief is **NOT verified**.
No configured Groq vision model was established. Universal access to private,
protected, region-blocked or deleted videos is not guaranteed.

The deployed-image dependency audit reported three advisory entries across
`python-jose 3.5.0` and `ecdsa 0.19.2`, with no available fixes reported. Auth
regressions cover DER/HMAC confusion rejection and use of the cryptography ES256
backend. This is mitigation evidence, not a claim that all vulnerabilities are
eliminated. Pattern-scan findings were reviewed as heuristic false positives.
Previously pasted private keys still require rotation by their owner.

Mobile-data connectivity and Play Store release signing were **NOT RUN**.
An already-mounted Collections tab requires refresh to show newly saved Library
items; this remains a deferred observation.
