# Remaining FindBack phases

Work on one phase at a time and report before moving on.

3. **Topic filtering — verified:** original keyword grouping is superseded by Phase 4 semantic labels. Specific names remain in Entity and detailed tags remain for search. See `PHASE4_VERIFICATION.md`.
4. **Saved dates, title styling, and semantic topics — verified:** localized dates and primary-colored titles preserve the existing design. Topics come from the existing LLM Brief call, using a shared broad-subject catalogue; no keyword inference or Other bucket. Existing three reels received backed-up, real-LLM topic metadata repair. See `PHASE4_VERIFICATION.md`.
5. **Processing and refresh UX — verified:** a light follows existing card borders while processing; top processing count replaces refresh button; short/empty lists support pull-to-refresh with a subtle cue. Automatic refresh and border animation stop when a real Brief is finalized. See `PHASE5_VERIFICATION.md`.
6. **Android sharing — verified:** fixed pre-listener share loss; live cold/warm shares and twenty-link batch mapped to existing records without changing saved Briefs. Android controls target ordering; pinning is user-controlled where supported. See `PHASE6_VERIFICATION.md` and `NATIVE_SHARE.md`.
7. **Optional accounts and account isolation — verified locally:** optional Supabase email/password accounts, isolated guest staging and per-account SQLite queues/caches; independent ordered top-bar slots; three-second refresh hint. Real user signup/login/reset and second-device synchronization still require user validation. See `PHASE7_VERIFICATION.md`.
8. **Focused runtime and cleanup audit:** investigate API/database/worker messages and processing failures; remove files only when verified unused. Preserve user data and avoid unrelated rewrites.
9. **EC2 and Supabase deployment — verified:** hosted HTTPS API, worker/outbox/private Redis, copied application PostgreSQL with complete migration fingerprints and client access restrictions, real-LLM article processing/search, and hosted phone APK. Local database retained for rollback; local writers stopped. See `PHASE9_DEPLOYMENT_VERIFICATION.md`. Real account email flows, mobile-data-only use, and EC2 video transcription remain unverified.

The user moved VM/Supabase database deployment to the end. The hosted app now uses EC2 and Supabase PostgreSQL/authentication. The preserved local database remains a rollback snapshot, not a synchronized replica.
