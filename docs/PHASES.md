# FindBack phases

Consolidated from the previous setup, architecture, product, implementation,
evaluation, verification and deployment reports. Earlier work reused phase
numbers in different tracks; the tracks below keep their meanings separate.
Historical “verified” status records earlier evidence, not a fresh production
check. Source code, migrations and tests describe current behavior.

## Core backend: phases 0–20

| Phase | Scope and status |
| --- | --- |
| 0 | Baseline inspection; the initial Windows Python environment blocked part of the suite. Superseded by subsequent full local runs. |
| 1 | Split shared content assets from owner-specific memories; preserve existing saves during migration. |
| 2 | Canonical content deduplication with database-enforced identity. |
| 3 | Repeat saves update the owner's memory/save count rather than duplicate it. |
| 4 | Privacy-aware reuse: only public content crosses owners; unknown/private stays isolated. |
| 5 | Transactional processing outbox; Redis outages retain work for dispatch. |
| 6 | Atomic processing claims, job states, idempotent retries and backoff. |
| 7 | Separate fetch/chat/embedding concurrency and rate controls; later extended to shared Redis capacity budgets. |
| 8 | Resumable FETCH → NORMALIZE → UNDERSTAND → BRIEF → CHUNK → EMBED stages. |
| 9 | Validated structured Brief contract with safe degraded output. |
| 10 | Recipe, product, tutorial, list and general extraction profiles. |
| 11 | Searchable chunks with source/video timestamps. |
| 12 | Vector + lexical retrieval fused by RRF; owner filters apply before candidate limits. |
| 13 | Private notes and “why I saved this” intent included in retrieval. |
| 14 | Provider abstraction, privacy filters, retention and shared-asset-safe deletion. |
| 15 | Flutter offline-first capture, cache, queue/sync and share method-channel intake. |
| 16 | Subject-based identity, verified legacy binding, owner-enforced data access and migration checks. |
| 17 | No separate verified phase-17 deliverable was identified in the retained reports/tests. |
| 18 | Metrics, private structured logs, claim timestamps and crash recovery diagnostics. |
| 19 | Search/AI evaluation harness; report quality measurements separately from test pass counts. |
| 20 | Disposable load/failure tests: burst intake, duplicate suppression, queue outages and interrupted jobs. |

Early provider evaluation exposed reader/proxy outages, captions mistaken for video
evidence, weak entity grounding and stuck jobs. Later Brief versions added
worker-only media acquisition, English/Arabic speech/OCR, segment-grounded points,
source evidence, retries, and attempt fencing. Live examples passed earlier
checks, but inaccessible/protected media and semantically incorrect citations
remain possible. Evaluation results depend on corpus, models and provider quota;
a passing fixture suite is not universal extraction accuracy.

## Reliability and mobile refinement: phases 1–9

| Phase | Scope and recorded result |
| --- | --- |
| 1 | Queue recovery and continuous ADB forwarding after device reconnect; verified with previously stranded phone saves. |
| 2 | Search/filter privacy fixes, whole-library metadata inventory and local fallback; recorded backend/device checks. |
| 3 | Broad topic grouping; its keyword/“Other” approach was superseded by phase 4 semantic labels. |
| 4 | Localized save dates, title treatment and broad LLM topics; entities remain separately searchable. |
| 5 | Processing borders/counts, lifecycle-aware polling, short-list refresh and multi-link intake. |
| 6 | Android cold/warm share buffering, consume-once delivery and native regression; live batch verification recorded. |
| 7 | Optional Supabase accounts, separate guest/account SQLite scopes, imports and ordered status indicators; provider/device flows still need manual validation. |
| 8 | Runtime audit and confirmed-unused-file cleanup; continued by the current repository review. |
| 9 | EC2 + Supabase deployment with migration fingerprints, HTTPS and retained local rollback database; earlier hosted smoke verified processing and isolation. |

## Public-testing work: phases 1–5

| Phase | Scope and recorded result |
| --- | --- |
| 1 | Read-only launch audit: dependency advisories, URL safety, intake cost, retries and inadequate TikTok evidence. |
| 2 | Public URL/DNS/redirect guards, dependency updates, shared intake/provider quotas and capacity deferral. |
| 3 | Resolve short video aliases before acquisition; reject navigation-only evidence; bound/probe media; grounded optional visual observations. |
| 4 | Private Library/Collections navigation, local guest collections, account sync/import and owner-scoped membership; collection deletion preserves memories. |
| 5 | Full local checks, earlier hosted release/backup and disposable smoke. Accurate live TikTok visual analysis was blocked by Gemini quota; physical Collections interactions/mobile-data use remain unverified. |

CI/CD subsequently added immutable ECR images and serialized SSM deployments after
backend/mobile checks. Rollback restores the previous image/configuration; schema
rollback and backup restoration require separate validation. Earlier dependency
audits still reported python-jose/ecdsa advisories without available fixes; auth
regressions mitigate known algorithm-confusion paths but are not a full security
audit. Rotate any private credentials previously shared outside secret storage.

## Flutter redesign: phases (a)–(f)

| Phase | Current scope |
| --- | --- |
| (a) Tokens | Blue Material theme, system appearance default, stronger control borders, rose Recipe color and Arabic fallback. |
| (b) Components | Accessible memory cards, real edit/retry/keep-link actions, save sheet and Undo feedback; light/dark/RTL/large-text checks. |
| (c) Home + Find | Full-screen focused Find, up to six nonempty data-derived chips, combined filters, explicit matched terms, Reading count and first-save state. |
| (d) Detail + reminders | Numbered grounded points/timestamp links; original/share dock; persistent absolute UTC/IANA reminders, contextual permission, local delivery/reconciliation. |
| (e) Account | Auth flows, appearance, opt-in weekly schedule, complete JSON export and provider-aware permanent deletion; retain other owners' data. |
| (f) Notifications | Open tracking, forgotten definition (>7 days, never opened, active owner save), frozen snapshots, timezone-aware weekly tick, device tokens and generic FCM push; tap opens the counted list. |

Server soft-delete retention is 30 days; visible Undo lasts five seconds or ten
with accessibility navigation. Explicit re-summarizing requires consent before
replacing edits and restores previous Brief/vectors/chunks on failure. Existing
server-memory deletion while offline remains device-only; no durable remote-delete
queue is claimed. Guest data is device-local; uninstalling without export loses it.

## Schema milestones

- `0001`–`0011`: core content/memory privacy, jobs, stage progress, search and identity.
- `0012`–`0013`: evidence/Brief storage and fenced worker attempts.
- `0014`–`0017`: collections, undoable deletion, private edits/link-only and durable reprocessing snapshots.
- `0018`–`0019`: reminders, account preferences and deleted-identity protection.
- `0020`–`0024`: first-open timestamp, frozen weekly snapshots, unique owner/week claims, private device registrations and confirmed delivery timestamp.
- `0025`: reconcile earlier snapshot schemas without losing retained history; keep owner/account cascades.
- SQLite version 7: capture/open event kinds; existing queued captures are preserved.

## Current review and remaining validation

The October 9, 2026 review fixes FCM wire/error contracts, weekly windows crossing
hours/days, snapshot database privacy and original counts after save deletion,
Firebase listener initialization and
registration/sign-out races, terminal open-event cleanup, failed-deletion recovery
and share-channel rebinding. It consolidates documentation and removes the unused
Expo client, local agent tooling from Git, and generated verification artifacts.
Dependency lockfiles and source licenses are retained.

Remaining validation: live FCM/APNs delivery with configured credentials, iOS build
and Share Extension, physical reminder/reboot/permission behavior, real account
email/recovery and multi-device flows, mobile-data-only use, production load and
backup restore drill. Android release signing must replace development signing.
Weekly hard crashes may miss that week's note; stale claims are released after
30 minutes, after the normal send window. Delivered snapshots are retained until
account deletion; delivery confirmation is best-effort, not exactly-once proof.

Run the checks in [README.md](../README.md). No production database migration,
cloud deployment, device installation or Git push is part of this cleanup.
