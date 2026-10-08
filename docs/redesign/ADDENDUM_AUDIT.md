# Addendum audit and Home + Find execution plan

Precedence: addendum > brief > visual prototype. The owner authorized decisions and continuous execution on 2026-10-08. This supersedes intermediate approval gates; changes remain scoped to Home + Find and its token/component/backend prerequisites. Collections screen and service remain untouched. Existing uncommitted work is preserved; no commits or deployments.

| Area | Current implementation | Required change | Backend gap / disposition |
| --- | --- | --- | --- |
| Home/navigation | Inline search, topic scroller, two destinations, separate Q/P pills | Full-screen Find, centered Save, Reading N, first-save panel | Existing processing state sufficient; preserve upload and polling |
| Find | Semantic backend, lexical evidence in match_reason, metadata filters; saved_after prerequisite complete | Wrap <=6 nonempty chips, immediate focus, real matched terms only | Add explicit matched_terms; count topic/type/date choices from the existing whole-library metadata scan (no invented recommendation API) |
| Shared cards | Edit/Delete callbacks, processing/failure styles | Summarize again; local queued copy; description-only flag; screen-reader actions | Add computed edited/source-quality flags and reprocessing state to item/search responses |
| Duplicate/share | Normalized URL deduplication returns existing id; duplicate dialog | Already saved toast + View; Saved. Reading it now. | Existing ingest duplicate contract sufficient |
| Deletion | Server restore/purge both expire at five seconds | 30-day restore/purge; 5-second toast, 10 seconds with screen reader | Change shared server retention constant; local Undo preserves queued rows and waits for in-flight uploads |
| Reprocessing | Retry restricted to failed/degraded items; edits are private overrides | Explicit Summarize again with replacement confirmation; old brief retained on failure | Add owner-scoped endpoint, durable previous-brief snapshot, edited-item protection for automatic paths |
| Tokens/theme | Soft borders, amber Recipe, Light default | Control border, rose Recipe, Auto default | Device only; persist existing explicit choices |
| Arabic/RTL | Latin UI and serif fonts; system fallback available but unverified | Directional new layout; bundled Arabic fallback; scale 1/1.3/2 at 320/360/390dp | No backend gap; verify mixed-script rendering |
| Memory/reminders (later d) | Detail/legacy actions; no reminder model or scheduling endpoint | Real device-zone/DST slots; one absolute timestamp+IANA zone; contextual permissions | Missing model, scheduling/delivery and permission handling; not silently simulated in Home + Find |
| Account (e) | Existing authentication and Appearance retained; settings/export/deletion implemented | Weekly controls, JSON export and confirmed deletion; large text and RTL | Contracts added in ACCOUNT_API_CONTRACT.md; provider deletion requires backend-only Supabase admin configuration. Weekly delivery remains phase (f) |
| Notifications (later f) | No notification transport/delivery | Private title-free payloads, snapshot Worth another look, zero-count skip | No opens tracking, weekly snapshots or push transport. Forgotten default: saved >7 days and never opened; current model cannot establish never opened. Needs explicit opens tracking and delivery setup |
| Collections | Existing tab/screen | Out of scope under addendum | No edits to its page, service, grouping or endpoints |

## Execution plan

1. Backend prerequisites: computed edited/description_only flags; explicit lexical matched_terms; owner-scoped reprocess with snapshot and edited safeguards; restore/purge 30 days. Migration only for durable reprocessing snapshot if needed. No reminder/notification APIs.
2. Shared UI: stronger control borders, rose Recipe, persisted Auto default, Arabic fallback, custom semantic actions, queued/description copy and reprocess menu, accessible Undo duration. Preserve existing component callback conventions.
3. Home/Find integration: keep existing services/controllers/routing. Library launcher opens a full-screen Find route; chips wrap, select real filters and send date separately to the API. Preserve advanced filters as an additional existing capability. Show real match explanations, pending states, duplicate toast, save entry points, and wire all card actions.
4. Local actions: use SQLite row snapshots for immediate deletion/Undo (no local schema migration), serialize against upload mapping, preserve Collections rows. Guest finished items edit locally; server edits use the API and failures remain visible in the edit sheet. Existing offline server deletion remains explicitly device-only when unreachable, with its limitation reported.
5. Verification: meaningful backend/API/SQLite/widget tests; related then full backend and Flutter suites, analyze, debug APK, diff/compile checks. Capture Home/Find at 360/390 in both themes; check 320 and text 1/1.3/2 plus RTL. Do not report a phase complete without observed passing checks.

## Decisions

- Auto for new installs; existing stored choices retained.
- Soft deletion retention and restore eligibility: 30 days; visible Undo five seconds, ten for accessibility.
- Dynamic topics: top two classified topics counted from actual saved metadata; hide choices with zero standalone matches, cap six total.
- Video/Recipe/Product filter typed content_type, not guessed keywords; existing advanced filters retained without the old horizontal chip scroller.
- Existing match_reason remains a neutral explanation; only explicit lexical matched_terms produce a Matched line.
- No Collections membership mutation in the new undo path: hidden/missing items disappear naturally from existing reads and Undo restores them.

## Execution status

Home + Find and listed prerequisites implemented; see HOME_FIND_IMPLEMENTATION.md and HOME_FIND_API_CONTRACT.md. Backend full regression: 888 passed, 1 skipped. Layout/RTL/first-save tests: 26 passed. Flutter analysis clean and debug APK built. Later-phase gaps remain explicit above. No intermediate approval stop was used.
