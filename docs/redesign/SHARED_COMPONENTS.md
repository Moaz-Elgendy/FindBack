# Phase (b): shared Flutter components

- `ResultCard` now renders the type/category chip, relative age, serif Brief with
  three-line expansion, actual recipe/product numbers and a note when available,
  amber dashed processing state, and safe failed-link copy.
- Optional `onEdit`, `onDelete`, `onRetry`, and `onKeepLink` callbacks expose working
  actions to screen owners. Overflow, long press, right click, and Space open the
  same menu; Enter opens the memory. Buttons have semantic labels and visible focus.
- `EditSheet.show` accepts the current Title/Brief and an asynchronous save callback.
  Validation and API errors keep the user's changes in the form; successful saves
  close it. Cancel and back are blocked while saving.
- `DeleteToast.show` accepts an asynchronous restore callback and a remaining Undo
  duration, capped at five seconds. It expires even with accessible navigation and
  displays restoration errors. Start timing before the undoable DELETE request;
  pass only the remaining server window after it succeeds.
- The existing Save sheet retains multi-link/offline capture and the title-hint
  contract, adds Cancel, and uses themed inputs/buttons. No button wrapper or new
  package was needed.

ApiClient supports edit, retry, keep-link, undoable DELETE, and restore. Failure
and link-only state survive SQLite through the existing Brief payload; no local
schema migration is required.

Screen wiring belongs to subsequent phases. Existing screen deletion still uses
its permanent-delete service and confirmation flow. Guest/offline deletion,
queue restoration, and collection membership must be handled when wiring Undo;
the shared toast alone does not change those service behaviors. Backend migration
and service deployment are prerequisites for the new API calls.

The prototype's fake data, phone frames, CSS, and page-level theme toggle were not
used. Recipe/product figures are only shown when present in the real entities;
missing facts are not inferred. Processing motion uses the existing border
animation and stops under reduced motion rather than adding another animator.

Validation includes Flutter widget accessibility checks at 360/390px in both
themes with double text size. Reference captures are written to `/tmp` only when
`CAPTURE_REDESIGN=true`; they are review artifacts, not pixel-parity assertions.
The static mobile-audit script also scans the obsolete React client and treats
padding/font sizes as touch targets, so its output requires manual interpretation.

## Addendum updates

The addendum supersedes the original five-second server coupling above: server
restore/purge is now 30 days, while DeleteToast lasts 5 seconds or 10 seconds with
accessible navigation and announces Undo. ResultCard now supports onSummarize,
ordered between Edit and Delete, and exposes custom screen-reader actions.
Queued/description-only copy and honest matchedTerms are supported. Recipe is
rose, controls use control-line, new installs default to Auto, and bundled
Noto Sans Arabic provides fallback coverage. See HOME_FIND_IMPLEMENTATION.md.
