# Phase 4 — UI and Collections verification

Verified on 2026-10-08 against source and isolated test databases. No production migration, deployment, APK installation, Git push, or phone test was performed.

## Changes

- Library/Collections bottom navigation; Library remains mounted so search and scroll state are retained.
- Collections use thumbnail mosaics, with readable title covers when thumbnails are absent or fail to load. Empty, offline, light/dark and large-text states are supported.
- Manual create, rename, delete, and memory selection. Deleting a collection does not delete its memories.
- Suggestions require two distinct canonical memory URLs and use stored topics/tools; people/org names are excluded. Suggestions never move memories automatically. Claude tool names share a Claude suggestion.
- Guests store collections in their scoped SQLite database without contacting collection endpoints.
- Accounts keep offline edits locally and synchronize through the authenticated backend. Revision-checked acknowledgements preserve edits made during an outstanding request. Deleted local collections use tombstones until acknowledged.
- Guest import creates new collection UUIDs for the account and deduplicates repeated imports by origin ID. Membership uses canonical URLs so capture/server ID changes do not break it.
- Deleting a local memory prunes collection membership in the same transaction and preserves unrelated pending captures. The selection sheet removes references that no longer correspond to locally available memories.
- Existing blue theme retained; refined app-bar typography, outlined card corners/surfaces, title weight and spacing, and Full Brief list affordance. Hidden tags, saved dates, processing border and original-link action remain.

## Database/API

Additive Postgres migration `0014_collections` creates collections and membership tables, owner indexes and composite foreign keys enforcing both collection and memory ownership. RLS is enabled and public/anon/authenticated direct grants revoked; authenticated API queries scope records to the requesting user. Collection deletion cascades membership only; item deletion cascades its memberships. API: GET collection list, PUT owned collection snapshot, DELETE owned collection.

SQLite schema 5 adds one local collections table with membership URLs, dirty revision and deletion state. Upgrade tests preserve cached memories and queued captures.

Migration upgrade/downgrade was tested only in throwaway `fb_phase16_*` PostgreSQL databases. Downgrade removes collection data/tables, preserves saved memories, and can be upgraded again. Back up collections before any production downgrade. No migration applied to Supabase.

## Commands and observed results

```sh
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -q backend/tests/test_collections.py backend/tests/test_phase16_multitenant.py backend/tests/test_schema_parity.py
```

`75 passed in 24.04s`.

From mobile:

```sh
flutter test
flutter analyze
```

Final full suite: `+158 ~1: All tests passed!` (158 passed, 1 skipped). Analyzer: `No issues found! (ran in 3.6s)`.

Targeted commands also run:

```sh
flutter test test/collections_test.dart
flutter test test/collections_ui_test.dart
flutter test test/collections_test.dart --plain-name 'deleting a memory lets'
```

The deletion regression failed before the fix. Full suite after the fix passed. Other initial failures included absent collection API/modules, a shared in-memory test fixture, widget disposal timing, and migration-test URL masking; these were diagnosed and corrected. Existing tests were not changed to mask regressions.

```sh
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Exit 0. Existing CRLF files preserved.

UI/UX skill design-system search ran for the mobile knowledge library; website hero/testimonial suggestions were not applied to this existing Flutter app. Mobile audit script was also run:

```sh
python .agents/skills/mobile-design/scripts/mobile_audit.py mobile
```

Its heuristic reported FAIL (30 flags, 61 passing checks). Flags include interpreting 10px padding as a touch target and recommending Jest/Detox for Dart token storage. This is not an accessibility pass. New cover tests verify tap behavior and no layout overflow at 320px width and 2x text in both themes; the navigation test creates an empty collection and returns to Library. Real-device and screen-reader checks are NOT RUN.

## Focused review

A read-only reviewer found a P2: stale membership after memory deletion could permanently block dirty collection sync. A regression reproduced it, then transactional local membership pruning fixed it; the full suite passed afterward. No privacy-critical issue was established by that focused review. This is not a whole-repository security certification.

Deferred minor: an already-mounted Collections tab does not automatically reload suggestions on returning from Library. Pull-to-refresh reloads it. Simultaneous cross-device edits use the last server write; collaborative merging is not implemented.

## Next phase

Phase 5: full backend/release/security verification, resolve remaining source-quality limits, back up and migrate the real database, deploy verified backend and build/install/test the mobile app when the device is available. None of those release actions occurred in Phase 4.
