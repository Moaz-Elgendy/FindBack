# Phase 2 backend foundations

Work is on the feature branch; these changes have not been deployed.

## Cache boundary

New saves begin owner-scoped. A worker acquires a PostgreSQL advisory lock for the normalized URL, then tries anonymous extraction without cookies, account credentials, clipboard previews, or account evidence. Only meaningful anonymous content with a successful AI brief is published. Cached machine fields, vectors and chunks are copied into each personal save; notes and edited fields never enter the cache.

The default sliding lifetime is 30 days (`PUBLIC_CACHE_TTL_DAYS`). Each usable hit extends it. Hourly cleanup clears expired global payloads in bounded batches; saved Items, chunks, notes and reminders remain. Prompt, pipeline and embedding versions invalidate a hit.

Summarize again creates an owner-scoped processing asset, bypasses shared output and retains the prior brief until success. The UTC per-memory quota defaults to three (`SUMMARIZE_AGAIN_DAILY_LIMIT`); the locked memory row makes concurrent requests idempotent. Failure preserves the prior brief and edits.

Feed responses use account-specific ETags with `Cache-Control: private, no-cache` and `Vary: Authorization`. Matching `If-None-Match` returns 304.

Migration 0026 adds cache and regeneration columns/indexes without merging or rewriting existing saves. Downgrade removes only the new fields. Legacy URL identities still resolve after regeneration.

## Anonymous domain probes

Probed locally with no user cookies or credentials on 2026-10-10. These observations concern individual samples; the classification is per URL and result, never a blanket domain allowlist. No AI brief generation or cache publication was attempted during these probes.

| Domain | Observed result |
| --- | --- |
| YouTube | Public sample extracted 88 characters of caption text anonymously. |
| Facebook | A saved sample exposed 207 characters of caption text anonymously. |
| TikTok | Reader returned no text; anonymous media probe exposed an 82-character description. |
| Instagram | Sample returned no text and anonymous media extraction failed; remains UNKNOWN/PRIVATE. |
| Article | Wikipedia sample exposed 12,000 characters of article text anonymously. |

Mocked Facebook/YouTube regressions prove two accounts process a positively classified public URL once; a login-wall regression proves owner isolation. Live provider availability, region restrictions, removed posts and private content can change the result. An anonymously inaccessible URL never uses the global cache.

## Settings

See `.env.example`: `PUBLIC_CACHE_TTL_DAYS=30`, `PUBLIC_CACHE_CLEANUP_SECONDS=3600`, `PUBLIC_CACHE_CLEANUP_BATCH=200`, `SUMMARIZE_AGAIN_DAILY_LIMIT=3`.

## Verification

Local checks: Flutter analyze clean; Flutter tests 441 passed with one existing skip; Docker image built; Compose configuration and Python compilation valid. The combined phase regressions passed (100 tests), followed by 34 cache/worker/state tests after the pool fix, nine burst/limit tests, 14 legacy-backfill tests, and explicit advisory-lock retention/release verification.

The burst regression exposed a pool deadlock: acquiring a dedicated lock connection while retaining the session connection could exhaust the pool and leave jobs pending. Processing now binds its Session to the lock connection, using one pool slot through all commits. A one-slot pool regression was observed failing before the fix and passing afterwards; it also verifies lock exclusion across commits and release on exit.

The legacy-backfill fixture now removes the new quota columns before simulating the old schema. The migration remains a strict additive migration. CI must pass at the committed revision before Phase 3 begins.
