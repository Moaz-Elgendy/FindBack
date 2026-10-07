# Remaining FindBack phases

Work on one phase at a time and report before moving on.

3. **Topic filtering — verified:** original keyword grouping is superseded by Phase 4 semantic labels. Specific names remain in Entity and detailed tags remain for search. See `PHASE4_VERIFICATION.md`.
4. **Saved dates, title styling, and semantic topics — verified:** localized dates and primary-colored titles preserve the existing design. Topics come from the existing LLM Brief call, using a shared broad-subject catalogue; no keyword inference or Other bucket. Existing three reels received backed-up, real-LLM topic metadata repair. See `PHASE4_VERIFICATION.md`.
5. **Processing and refresh UX — verified:** a light follows existing card borders while processing; top processing count replaces refresh button; short/empty lists support pull-to-refresh with a subtle cue. Automatic refresh and border animation stop when a real Brief is finalized. See `PHASE5_VERIFICATION.md`.
6. **Android sharing — verified:** fixed pre-listener share loss; live cold/warm shares and twenty-link batch mapped to existing records without changing saved Briefs. Android controls target ordering; pinning is user-controlled where supported. See `PHASE6_VERIFICATION.md` and `NATIVE_SHARE.md`.
7. **Profiles and account isolation:** add individual Supabase sign-in/profile flows, ES256 token verification, and isolate offline caches/queues across accounts. Preserve existing memories through an explicit, verified ownership transition. Requires the public Supabase configuration locally.
8. **Focused runtime and cleanup audit:** investigate API/database/worker messages and processing failures; remove files only when verified unused. Preserve user data and avoid unrelated rewrites.
9. **Final deployment — free VM and Supabase database:** prepare production Compose, HTTPS, private Redis, ES256 token verification, and backed-up database migration. Provide manual Oracle/free-VM/Supabase setup steps then. Deploy only after the earlier phases are verified and authentication is ready.

The user moved VM/Supabase database deployment to the end. Local development continues until that final stage; adding the project URL does not change the running database or enable individual accounts.
