# Memory detail and reminders

Scope: brief phase (d) plus its missing reminder persistence and native delivery prerequisites. Collections and weekly notes stay untouched. The owner authorized implementation and independent decisions; no further approval gates.

Reuse existing Flutter routing, ItemsService, MemoryActions, EditSheet and DeleteToast. Detail gets its reference layout, numbered points and timestamp links, Edit/Summarize again/Delete, a reminder sheet, and Open original/Share dock. Preserve Cook Mode, ingredients and copying.

Store one reminder per server item: absolute UTC time, IANA zone, delivery acknowledgement. Owner-scoped GET/PUT/DELETE and list endpoints validate future times and zones. A conditional acknowledgement must not consume a newer replacement. Migration 0018 adds a separate reminder table, cascading on item deletion. No new infrastructure.

A SQLite reminder mirror supports guest memories and native scheduling. Use flutter_local_notifications for private Android / title-free iOS notifications; flutter_timezone and timezone for current IANA zone and calendar/DST slot computation. Ask permission only on first reminder action after context. Store denied reminders; retry scheduling on resume. Persist notifications before native calls and reconcile overdue reminders on resume, including >24h late. Notification taps open the appropriate detail route. Cancel account notifications on sign-out and memory notifications on delete, reschedule on Undo.

Native OS delivery works without an API connection. Local native scheduling avoids requiring unconfigured push credentials. Precision is subject to OS notification/alarm settings; no exact-alarm permission prompt. Reconciliation recovers overdue reminders when the app resumes. Physical reboot and iOS delivery require device validation.

Tests: UTC/zone API validation, tenant isolation, conditional acknowledgement, overdue persistence; late-night/18:30/17:30/Saturday/Sunday/DST slots; permission-denied persistence and settings action; scheduling/cancellation/Undo; title-free payload; detail flows, both themes, 320/360/390dp, scales 1/1.3/2 and RTL. Run focused then full backend/Flutter suites, analyze, debug APK and diff checks. No deploy/commit.
