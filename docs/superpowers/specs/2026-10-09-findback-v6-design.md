# FindBack v6 reliability and design alignment

## Intended outcome

Implement every area in the attached request. `findback-v6.html` is the visual
reference for Flutter, including both palettes, typography, spacing, cards,
collections, account, reminders, menus, sheets, and toasts. Explicit requirements
in the request override prototype behavior (confirmation before deletion, expanded
detail menu, sharing, and hiding the weekly time row when disabled).

## Investigation evidence and outstanding diagnosis

- Flutter already has SQLite captures, retrying sync, guest authentication,
  account-scoped storage, reminders, and cached memory details.
- FastAPI already has ContentAsset, UserMemory, Item, ProcessingJob, a transactional
  outbox, worker heartbeats and recovery, collections, exports, and account deletion.
- Existing ContentAsset deduplication is privacy-scoped. PUBLIC content can be
  reused; UNKNOWN and PRIVATE content belongs to its owner. Preserve that boundary.
- The current dark palette differs from the reference. Detail has a plain action
  menu, standalone copy button, and source-URL sharing. The feed has RefreshIndicator
  instances and repeatedly scans all pages during processing refresh.
- Local Docker has only PostgreSQL and Redis running. No local API, Celery worker,
  or dispatcher process was found. `localhost:8000/health` refuses connections.
  This is evidence of a local deployment failure, not yet proof of the reported
  phone bug. Device log queries did not provide a useful reproduction.
- Capture queues retryable API errors, and guest session issuance currently maps
  every DioException to connectivity failure. Distinguish rejection, rate limits,
  authentication, service unavailability, and actual transport failures.

Before fixing the processing bug, reproduce using the actual build configuration,
trace capture → guest/auth headers → sync mapping → job creation → dispatcher →
worker → extraction/AI → per-user copy → local refresh. Record the failing boundary
and add a regression test there. Do not claim a root cause from the UI message alone.

## Implementation approach and scope

Extend existing services and models. Keep durable queues and recovery; avoid a
second queue or parallel memory system. Changes must reach Flutter models, SQLite
migrations, API parsing, capture/sync/actions, all relevant screens, native link
handlers, backend schemas/routers/tasks/services, Alembic migrations, deployment
configuration, tests, and documentation. Preserve existing saves and edited copies.

### Reliable saves and quiet refresh

Represent queued, uploading, reading, done, and failed consistently across API and
local storage, mapping existing internal states where possible. Persist upload
outcomes and attempts; never discard offline captures based on age or retry count.
Separate device offline status from server/auth/processing errors. Log IDs, states,
attempts, durations, and safe error categories without tokens or saved content.
Keep outbox recovery bounded and observable. Failed cards expose Retry and Keep
link only through the existing actions layer.

Render SQLite first. Coalesce refreshes on launch, foreground, save, sync, and
completion. Replace visible refresh indicators with silent refresh. Add conditional
library requests whose validator changes for edits, status, reminders, and deletions,
not only creation timestamps. Poll only while server items are processing, with
backoff and lifecycle cancellation. Local queued rows rely on sync retry triggers,
not repeated whole-library fetches. Fetch collection counts only when invalidated.

### Global results and personal copies

Use a globally unique normalized public URL for reusable processing results, with
30-day sliding expiry and last-hit time. Use existing asset/job locking and unique
constraints to ensure concurrent saves attach to one active processing job.
Store only extracted public source content and machine output globally: never hints,
reminders, sender names, user edits, or account-specific metadata. UNKNOWN/PRIVATE
content remains owner-scoped until positively classified public.

Improve canonicalization conservatively: remove known trackers/fragments and
default ports, normalize supported YouTube variants, retain meaningful query/path
semantics, and test collisions. Do not merge existing user saves destructively when
normalization changes. Account for legacy canonical values during lookup/migration.

Only successfully completed, usable results are hits. Failed and low-quality
results may be replaced by the next normal save. Expiry cleanup removes the global
payload without deleting personal copies, reminders, collections, or search data.
Reuse the existing scheduler/dispatcher for periodic cleanup. TTL checks remain
authoritative even if cleanup runs late.

### Summarize again

Manual regeneration bypasses the global cache and targets one user's memory.
Its task must not fan out to other items sharing an asset or overwrite global
output. Keep the previous brief until success; restore it and expose a friendly
error on failure. Preserve the existing explicit replace-edits check. Add an atomic,
configurable per-memory daily limit, default three, enforced across worker processes.

### Explicit snapshot sharing

Authenticated senders create a sanitized immutable snapshot with a cryptographically
random token. Store the token hash, creator, expiry, revoked state, and snapshot.
Default expiry is 30 days, configurable. Provide creator-owned revocation and
creation/redemption limits. Snapshot includes title, brief, safe source metadata,
and public URL; excludes reminders, search tags, collection membership, hints,
raw extraction, private asset references, credentials, and other memories.

The public HTTPS landing route exposes no memory content. App Links open the app;
fallback presents installation through one configurable Play Store URL. Do not
redirect to an invented store listing when it is unset. Publish assetlinks.json
from configured package and signing fingerprints. Add iOS association configuration
if the project has a usable signing/domain setup; document any deployment dependency.

Handle cold and warm links, persist a pending token across sign-in, then redeem
through an authenticated endpoint. Redemption creates an independent personal copy
without AI, labels it Shared by the sender's chosen public name, and is idempotent
for the same recipient/token. An already-owned source opens the existing card without
overwriting edits. Source memory deletion does not invalidate an explicit snapshot;
revocation, expiry, and sender account deletion do. Sharing sheets also offer normal
source-URL sharing. Signed-out senders go through sign-in before creating a snapshot.

### Flutter design

Centralize every reference color and dark danger override in the existing theme;
use Schibsted Grotesk and Source Serif 4 already installed. Keep Auto as persisted
ThemeMode.system. Match the reference card meta row with an ellipsized single-line
chip, aligned time and top-right actions. Rebuild preview rows, failed cards, dashed
reading skeletons, Reading N pill, and Library/+ /Collections navigation.

Use shared, themed anchored overlays with viewport-safe positioning and accessible
focus/dismiss behavior. Feed contains Edit/Delete; detail contains Edit/Copy summary/
Summarize again/Delete. Shared delete confirmation stores an on-device opt-out and
Account provides a reset row. Account deletion never inherits this opt-out.

Match the fixed detail dock, numbered points, timestamps, Jump to links, reminder
button and real-time choices. Copy clean title, numbered points, optional timestamps,
and source URL; show themed success toast. Move debugging tags below all other
content, muted and collapsed, controlled by one flag and a public-launch TODO.

Match automatic collection cards/counts and navigation; verify grouping persists
and excludes deleted/link-only/failed records as appropriate. Match Account groups,
export action, signed-in state, appearance segments, weekly toggle and conditional
Sunday time row. Signed-out authentication fits the same tokens and input styles.
Update privacy text to say sharing happens only through an explicit user action.

### Account deletion and export

Audit existing provider-first deletion and local teardown ordering. Hard-delete
credentials/sessions at the identity provider and all personal backend records,
shares, device tokens, scheduled reminders, and notification snapshots. Keep public
global results. Remove local account DB, secure state, preferences, pending links,
and scheduled notifications before returning to a clean signed-out scope. Require
typing DELETE and a final explicit confirmation. Make retries safe if provider
deletion succeeds before DB deletion. Verify export contains the user's own copies
and source URLs, including locally queued saves, without other users' data.

## Migration and deployment

Add reviewed Alembic migrations for global expiry/quality state, sharing snapshots,
per-memory regeneration limits/isolation, and any revision tracking needed for
conditional feed requests. Backfill safely; never collapse/delete existing saves.
Preserve FK ownership isolation and add indexes for expiry and token lookup.
Add SQLite migrations for new sync/share metadata and confirmation preference.

Document new settings in env examples and README: public share origin, share expiry,
Play Store URL, Android package/fingerprints, optional iOS identifiers, regeneration
limits, and cache TTL/cleanup cadence. Exact setting names follow existing conventions
in the implementation plan. Verify API/worker/outbox/beat startup and failure reporting.
Real App Links verification requires the deployed HTTPS host and release fingerprint.

## Verification and final report

Backend tests: URL equivalence and collision protection, usable cache hits, sliding
TTL and expiry, concurrent saves/jobs, personal edit isolation, failed-result retry,
manual regeneration isolation/limits/rollback, share snapshot sanitization,
expiry/revocation/idempotency/auth/rate limits, deletion coverage, conditional feed
invalidation, and weekly scheduling. Reuse existing PostgreSQL fixtures.

Flutter tests: queued-save recovery and status mapping, connectivity versus API
errors, menu bounds/actions, confirmation opt-out/reset, copy format, reminder times,
theme persistence/system changes, weekly visibility, share continuation, collections,
and account cleanup. Run flutter analyze, Flutter tests, and backend tests.

Compare each screen against all seven HTML sections in both themes at reference
width and narrow widths, with larger text and keyboard visible. Check empty/error/
offline states, dialogs, overlays, toasts, and accessibility. Add a manual checklist
for real-device sharing, foreground/background capture, notifications, and native
link verification. Clearly distinguish automated evidence from untested deployment
and device behavior.

Final report includes the reproduced root cause, changes per requested area,
migrations/configuration, test evidence, remaining limitations, and extra fixes.
