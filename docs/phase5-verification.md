# Phase 5 — immutable memory sharing

Commit `8c9930d`; CI passed. No production deployment.

Backend revision `0027_memory_sharing` adds optional account display names,
recipient attribution, hashed opaque share tokens and independent redemption records.
The token is returned once; active-link management shows title/expiry and can revoke.
Snapshots whitelist title, current brief, points and timestamp-only references,
content type and credential-free source URL. No sender email, notes, reminders,
search tags/hints, extraction, embeddings or private asset pointers are copied.
Recipient copies are ordinary owned Items with an independent searchable document.
They require no AI request or worker job. Already-owned sources keep their edits.

A snapshot survives source deletion. Expiry/revocation/sender account deletion
prevent later redemption; recipients' already-saved copies are never affected.
Sender snapshots are limited to 20 per rolling day and new redemptions to 100.
Concurrent redemptions are serialized per account, and competing ordinary saves
win without having their content overwritten. Bearer share tokens are masked in
API access logs without changing Uvicorn's positional formatter arguments.

Flutter uses the existing native link channel for cold/warm Android HTTPS links.
Pending tokens live in encrypted device storage, scoped by API origin, across
sign-in and app reconstruction. The source share sheet remains available.
Sharing settings are a separate Account page with optional display name and active
link revocation. Shared cards use a neutral attribution when no name was supplied.
Attribution is stored inside the existing version-8 SQLite brief payload, which
preserves existing databases without a needless new column or schema migration.

## Configuration and deployment

- `SHARE_LINK_TTL_DAYS=30` is the single expiry setting.
- `SHARE_LINK_ORIGIN` must match the compiled API origin and Manifest App Link host.
- `ANDROID_APP_LINK_PACKAGE` defaults to `com.findback.findback`.
- `ANDROID_APP_LINK_FINGERPRINTS` supplies comma-separated SHA-256 certificate fingerprints.
- `ANDROID_PLAY_STORE_URL` is optional; an unset/invalid URL produces no listing link.

`GET /.well-known/assetlinks.json` is empty until real fingerprints are configured.
`GET /s/{token}` exposes no memory content and offers app opening and the configured
Play Store listing. Revoked/expired/deleted links cannot return a snapshot.
The current Android release build still uses the repository's debug signing key;
use the installed certificate for device verification, then publish the actual
release certificate when release signing is established. Never invent a listing.

There is no configured iOS development team or usable signing setup in the project.
iOS association and device verification require that setup and are deferred per
the approved optional iOS scope. Android host verification requires deployment
of this branch's association route and the actual certificate configuration.

## Verification

- New migration fixtures retained full schema parity and downgrade/reapply checks:
  64 tests passed, including sharing tests at that checkpoint.
- Sharing security/ownership/expiry/concurrency suite: 13 passed.
- Access-log and existing log-filter regressions: 19 passed.
- Native Kotlin boundary check passed for cold/warm share/recovery buffering.
- Final full Flutter suite: 463 passed, one existing skip; analyze reports no issues.
- Debug Android APK built; full-app pending-token/sign-in/cache regression passed.
- Full backend suite: 1130 passed, one existing skip; final failed-brief/source-metadata regressions passed afterward. Final sharing/access-log targeted rerun: 14 passed. Native boundary rerun passed. CI https://github.com/Moaz-Elgendy/FindBack/actions/runs/38039569558 passed: 1131 backend and 463 Flutter tests, one existing skip each; analyze clean.

No live AI processing was used for snapshot redemption. Recipient snapshots use
lexical search; they intentionally do not reuse private embeddings. Semantic
indexing of sanitized snapshot text would require a separate embedding call.
