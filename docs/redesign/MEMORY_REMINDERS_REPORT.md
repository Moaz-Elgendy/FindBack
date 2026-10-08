# Phase (d): Memory detail and reminders

Implemented the numbered summary, source/date metadata, real video timestamp links, Edit/Summarize again/Delete actions, shared Undo, reminder sheet, and Open original/Share dock. Existing tags, ingredients, Copy summary and Cook Mode remain available. Collections UI was not changed.

Reminders persist locally before requesting notification permission. Guest reminders, offline changes, account import, Delete/Undo, timezone travel, remote replacements and overdue reconciliation are covered. Native payloads use generic private notification text and the memory identifier, without memory titles. Cold notification launch intents are preserved.

Backend migration `0018_reminders` adds one reminder per item with cascading purge, UTC schedule, IANA timezone and delivery acknowledgement. Owner-scoped reminder routes support reading, setting, removing and acknowledging reminders. Acknowledgement requires the exact due schedule, protecting replacement reminders. Deleted items are excluded. Mobile SQLite advances to version 6. Migration files are provided; no application database migration or deployment was performed.

## Verification

- Backend: `SUPABASE_URL= TEST_DATABASE_URL=postgresql://findback:findback@127.0.0.1:55433/fb_reminders_validation .venv/bin/python -m pytest -q` — **891 passed, 1 skipped**.
- Mobile: `flutter test --reporter expanded` — **274 passed, 1 skipped**.
- Layout capture: `flutter test test/memory_detail_redesign_test.dart --dart-define=CAPTURE_REDESIGN=true --reporter expanded` — **24 passed**; light/dark phone screenshots inspected. Tests include large text and RTL.
- `flutter analyze` — **No issues found**.
- `flutter build apk --debug` — **built successfully**.
- `python3 -m compileall -q backend/app backend/alembic/versions/0018_reminders.py` — passed.
- `git diff --check` — passed after correcting line endings on two added lines.

## Remaining checks and limits

iOS compilation, physical-device permission/reboot delivery and screen-reader checks were not run on this Linux host. Android notifications use inexact scheduling, so the OS may delay delivery. The compatible notification package is pinned to `23.0.0-dev.3`; stable v22 conflicts with the existing connectivity dependency. Delivery uses native scheduled notifications and resume reconciliation; no server push infrastructure was introduced. Account/weekly-note phases remain outside this phase. Video duration is omitted because reliable duration metadata is unavailable.
