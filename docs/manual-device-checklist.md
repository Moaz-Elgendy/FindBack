# Final device checklist

Run after deploying the feature branch, backing up PostgreSQL and running
`alembic upgrade head` (0027). Install the configured APK without clearing data.
Use disposable registered accounts for destructive checks; preserve real saves.

- [ ] Confirm the compiled API URL is `https://findback.duckdns.org`. Check
  `/ready` reports API, database/schema, worker, dispatcher and beat healthy.
- [ ] Save a fresh public article using Android Share and the center + sheet.
  Observe upload → reading → ready, automatic feed updates, and no refresh spinner.
- [ ] Save while genuinely offline, restart FindBack, then reconnect. The capture
  remains on the phone and uploads automatically without duplicate cards.
- [ ] Stop the worker. Confirm `/ready` returns 503 and Library visibly reports
  processing unavailable while preserving cards. Restart it and verify recovery.
- [ ] Save a login-walled/unreachable page. Verify the reason/URL, Retry and Keep
  link only. Failed content must not become another account's successful cache hit.
- [ ] Save the same public Facebook/YouTube URL with two accounts. Check only one
  public processing result is generated. Test a login wall and credential-bearing
  URL separately; their results must remain owner-scoped.
- [ ] Review Library/Find/Collections/Memory/Account in Light, Dark and Auto,
  including a narrow screen, 200% text, keyboard opening, and RTL text.
- [ ] Check one-line chips, metadata alignment, three-point previews/expansion,
  reading/failed cards, amber Reading count, and bookmark/grid navigation.
- [ ] Open card/detail menus at screen edges. Verify exact actions, red Delete,
  confirmation opt-out, Account reset, Undo and accessible touch targets.
- [ ] Copy a memory; paste elsewhere and verify title, numbered points, optional
  timestamps and source URL. Check the success toast and fixed bottom dock.
- [ ] Set/cancel/change/remove a reminder. Verify real option times, denied-permission
  recovery, one notification, notification tap navigation and private lock-screen
  presentation. Check muted collapsed search tags remain at the bottom.
- [ ] Use Summarize again on one account's edited copy. Verify quota feedback,
  retained previous brief on failure and unchanged copies/cache for other users.
- [ ] Verify automatic collection counts and navigation, including preserved
  legacy named collections and the empty state.
- [ ] Change theme and restart; switch system theme while Auto is selected.
- [ ] Check signed-out sign-in/sign-up/password recovery. A pending memory link
  must continue after sign-in; switch accounts during redemption as well.
- [ ] Set a display name in Account → Sharing. Create a link and test cold/warm
  App Links with real certificate association. Save a recipient snapshot without
  another AI request; verify named/neutral attribution and no sender email.
- [ ] Edit/delete the sender's source and revoke/expire the link. Existing recipient
  copies stay unchanged; future redemption fails clearly. Check active-link list.
- [ ] Test fallback with the app absent. Configure a real Play Store URL only when
  a listing exists; verify no private memory content appears on the landing page.
- [ ] Export saves and inspect the file. Enable/disable the weekly note, choose
  its time, and confirm actual scheduled notification delivery and account scoping.
- [ ] On a disposable account, use the exact deletion warning and type DELETE.
  Verify provider/account data, reminders, sessions and local caches are removed;
  other accounts, recipients' saved copies and sanitized global cache survive.
- [ ] Simulate provider deletion failure on a test deployment. Verify the account,
  saved cards and raw snapshots remain intact and deletion can be retried.
