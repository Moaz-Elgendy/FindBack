# Phase 6 verification

Release `4b80e3f` passed [production CI/deployment](https://github.com/Moaz-Elgendy/FindBack/actions/runs/38049775779):
1137 backend and 470 Flutter tests passed, with one existing skip each.
The user approved merge and deployment; production schema is `0027_memory_sharing`.
The follow-up below records live checks without changing application behavior.

## Visual comparison

Read the entire supplied `findback-v6.html`, including its interaction script.
Rendered all seven sections in Chromium in both palettes at 360/390px.
Generated Flutter screenshots for Library, Find, Memory, Collections and Account
in both palettes at 320/360/390dp. Shared component captures include reading and
failed cards and edit/save sheets; widget coverage also exercises menus,
confirmations, toasts, 130%/200% text and RTL. Screenshots are retained locally under
`/home/moaz/.local/state/findback/verification/phase6/`.

The comparison found and corrected Account identity/theme row placement,
Memory title size/back navigation/reminder alignment, button font inheritance,
feed menu alignment at the content edge, and bottom navigation icon choices.
Large text still uses wrapping layouts instead of clipping controls.
Reference section 7 is Android's lock screen, not an application screen;
notification privacy/scheduling tests cover its application-controlled behavior.
Actual lock-screen presentation remains a device check.

## Boundary fixes found during final verification

- Provider deletion failure previously removed stored snapshots while retaining
  the live account. Provider deletion now succeeds before destructive snapshot
  cleanup. The regression checks the storage deletion calls, not just SQL rows.
- An account switch during share redemption could strand the same pending token.
  Completion now resumes that token after the account binding settles, without
  caching the first account's result in the second account.
- API-key/authorization/secret query parameters could pass the public-content
  classifier. A shared credential URL validator now prevents anonymous cache
  probing, publication, cache hits and share-link creation for those URLs.
  Ordinary owner-scoped processing still works independently for two accounts.
- Backend readiness was not observed by the Library during a worker outage.
  Processing feed requests now check `/ready`, including unchanged 304 feeds.
  A visible failure message preserves saved cards and clears after recovery.
  Ready feeds do not make this extra request; existing polling backoff remains.

Each behavioral boundary was reproduced with a failing regression, then fixed.
A fresh whole-branch static review supplied the deletion and account-switch
findings. The account-switch finding was treated as important because it blocks
an explicitly requested sign-in continuation. No review minor was deferred.

## Checks

- Full Flutter checkpoint: 468 passed, one existing skip. Final rendered/widget matrix: 32 passed.
  Full rerun after the offline-label fix: 470 passed, one existing skip.
- Readiness API/UI regressions: 2 passed.
- Account backend suite: 9 passed.
- Final credential-cache/processing/sharing suites: 41 passed.
- Initial full backend run: 1130 passed, one existing skip, one load-budget failure.
  Its first 10-save burst averaged 252ms against the unchanged 250ms limit while
  the emulator/build/screenshot work shared the host. Isolated rerun passed at
  54ms/save. Final full run: 1137 passed, one existing skip; first burst 44ms/save. No threshold or test was loosened.
- Final Flutter analyze: no issues.
- Compose configuration validated. Final backend Docker image built.
- Configured debug APK built with `https://findback.duckdns.org` and the existing
  public Supabase project configuration. Final rebuild after readiness changes
  passed. No service-role key is compiled into the APK.

## Device state and deployment prerequisites

ADB intermittently exposed Android 15 emulator `emulator-5554`. Installed the
Phase 5 APK with `adb install -r`, preserving data. Pixel Launcher/System UI then
reported ANRs, and the emulator repeatedly disconnected, including after reboot.
No FindBack database or account was cleared. The emulator later became stable.
The configured APK was installed preserving data; a fresh Testing effect article
reached a ready summary through Android Share and automatic refresh.
A stricter offline check blocked outgoing HTTPS with temporary IPv4/IPv6 firewall
rules. SQLite confirmed a local pending Metacognition save with no server ID.
It survived restart and completed automatically after both rules were removed.
The check found and fixed a misleading local card label: local pending saves now
say “Saved on this phone”, while server pending cards retain “reading it now”.
The regression was observed failing, then all four feed preview tests passed;
analyze and the configured APK rebuild passed.
Detail navigation, anchored menu and Android clipboard preview were exercised.
Dark mode persisted after restart. Screenshots are retained with the Phase 6 artifacts.

The original phone build used `http://localhost:8000`, with no flavor/override or
ADB reverse; only PostgreSQL/Redis were running. Phase 1 repaired and reproduced
that local path, with three saves reaching ready. Normal builds now default to
`https://findback.duckdns.org`; the final configured APK uses that deployed host.

Android verified links require deploying revision 0027 and its `assetlinks.json`
route with the real installed signing certificate. This debug APK's SHA-256
certificate digest is `f45e5d563ce68d79157f035b8834e8aae18a59230c975966ccad979813b7a55c`.
Use colon-separated bytes for `ANDROID_APP_LINK_FINGERPRINTS`; replace this with
actual release fingerprints before shipping. No Play Store listing or release
signing identity was fabricated. Optional iOS association remains dependent on
an Apple signing team. Production App Links are verified for the installed debug certificate. Real
password sign-in with preconfirmed disposable accounts passed; email delivery,
notification delivery and release-signing association remain unverified.

## Rulings retained from the plan

1. Classify public content in the worker rather than blocking ingestion on a
   network probe. This preserves quick durable saves; a first save waits for
   processing before another account can use the global result.
2. Recipient snapshots retain a sanitized lexical index and omit private
   embeddings. This avoids AI work/private search evidence at redemption;
   semantic-only retrieval needs a separate sanitized embedding operation.
3. Store attribution in the existing SQLite brief payload and pending tokens
   in encrypted origin-scoped storage. Older readers ignore attribution safely;
   no additional SQLite column is required.

The reviewer set credential-cache privacy and worker-down UI aside pending
stronger evidence; both became concrete, tested fixes above. Visual fidelity was
reviewed through rendered screenshots separately. Live provider/device behavior
remains explicitly unverified rather than assumed to pass.

## Approved deployment preparation

The production PostgreSQL server is version 17. A version-matched custom-format
backup was created and `pg_restore --list` validated it before migration:
`/opt/findback/backups/v6-20261010/before-v6.dump` (526678 bytes, SHA-256
`f037e8ac0029df7c8c1e8d9b476dafa88ad79a191c22636805ccefd457e120cc`).
The first attempt with PostgreSQL 16 correctly refused the version mismatch.
The installed APK certificate was added to the production App Link setting,
preserving existing fingerprints and a protected copy of the previous environment.
Before deployment, `/health` returned 200 and `/ready` returned 404.
The old backend also summarized a missing Wikipedia page; do not treat that probe
as a successful public-article extraction. Release `/ready` now reports API, DB/schema, Redis, worker, dispatcher and beat
healthy; `assetlinks.json` serves the installed certificate. Registered sharing and deletion checks now pass with the validated server-only
service-role key. The user accidentally created `/root/.env`; it was deleted at
their request. `/opt/findback/.env` remained complete. Its new key validated with
Supabase HTTP 200 before API/worker/dispatcher/beat were reloaded. Public `/ready`
reports every component healthy.

Live disposable-account checks passed: password sign-in, 30-day TTL, private
fallback, neutral/named attribution without sender email, idempotent redemption,
active-link listing, revocation, independent recipient copies, real provider
account deletion and recipient survival. They used a clearly marked ready fixture;
this does not establish successful AI ingestion. All disposable identities were
cleaned up, and the separate disposable guest probe was removed.

The first live AI ingestion attempt was paused by Gemini's configured daily
request limit (20/20). Groq is configured as the chat fallback and can complete
the summary, but Gemini embeddings still use the same exhausted quota. A pending
card had a summary and no embedding; this explains why chat fallback alone did
not finish processing. Embeddings are not silently replaced with incompatible
Groq vectors.

The user subsequently authorized raising `GEMINI_DAILY_REQUEST_LIMIT` to 30.
A protected environment backup was created, usage counters were preserved,
compose validated, and API/worker/dispatcher/beat reloaded. A fresh guest save of
`https://en.wikipedia.org/wiki/Serial-position_effect` reached `ready` through
the deployed HTTPS API; the disposable save was removed. A later recheck reports
Gemini usage 22/30 and every readiness component healthy. A physical Samsung SM-S731B was subsequently connected over wireless ADB.
The installed APK hash matches the tested debug APK; existing saves remained
visible. Android Share submitted `https://en.wikipedia.org/wiki/Generation_effect`.
The feed updated automatically to a complete summary; SQLite recorded the card
as `ready`. Worker logs referenced that exact card ID, and its production asset
and job reached `READY`/`EMBED`. Android verified the production domain on this
phone as well. A later direct item lookup was empty because API logs recorded
`DELETE /api/v1/items/<test-card-id>` returning 204. The sender did not delete
that memory: `GuestLibrary.cacheAndRelease` automatically releases temporary
server staging after caching a completed guest brief in SQLite. This is the
documented guest storage model, not a user deletion or lost local memory. The
existing guest-library regression verifies that caching precedes the DELETE and
that subsequent reads and the feed remain local. Guest-library/reprocessing
suites were rerun successfully, including preservation of edits and previous
briefs on failure. No storage behavior was changed. Actual notification delivery
and live sign-in continuation remain separate device checks.

Additional emulator checks passed: guest export opens Android's share sheet,
produces valid version-1 JSON with six saves, and includes no access-token,
refresh-token or service-role-key fields. No export was sent to anyone. Denying
notification permission displays an explicit warning and opens Android Settings.
Enabling notifications there, scheduling a reminder and removing it worked;
the pending alarm cleared and the UI returned to “Remind me”. Actual notification
delivery and lock-screen presentation remain unverified. Android reports
`findback.duckdns.org: verified`; cold/warm redemption across live sign-in still
needs an isolated device account that cannot migrate or delete real guest saves.

## Deployment OIDC repair

The first deployment attempt failed because GitHub issued immutable subject
prefix `repo:Moaz-Elgendy@297489873/FindBack@1393710703`, while IAM trusted the
old name-only prefix. Terraform now uses the exact immutable prefix plus
`ref:refs/heads/master`. A saved targeted plan was inspected: only the
deploy-role subject changed; principal, audience and permissions were unchanged.
The trust/pipeline regression was observed failing, then passed, and is now part
of CI. The retry deployed `285d023` successfully.

The first offline-label CI run also identified two existing tests still expecting
the misleading label. Both now assert the correct local boundary. The full local
Flutter suite passed 470 tests; the corrected follow-up passed the production CI gate linked above.
The rebuilt APK displayed “Saved on this phone” during the strict network block.

## Physical sign-in configuration correction

The physical phone reported “Account sign-in is not configured.” The APK used
the default production API but omitted both Supabase build defines. Normal
builds now default to the production Supabase URL and its public publishable
key, with existing define overrides preserved. The publishable key validated
against Supabase's public settings endpoint (HTTP 200). No service-role or
provider key is embedded. A default-constructor sign-in regression reproduced
the failure, then passed after the correction; the auth suite passed 14 tests.
Flutter analyze passed; the full suite passed 471 tests with one existing skip,
and a normal debug APK build passed without Supabase define overrides.

## Physical sign-in feedback follow-up

The user successfully signed in with the corrected build, then reported silent
rejected sign-in and signup confirmation. Both messages existed but were rendered
below the Account settings, outside the phone viewport. Two widget regressions
reproduced that placement at 360 by 800 pixels. Feedback now appears beside the
authentication form, and submission dismisses the keyboard. Auth and Account
suites passed 53 tests. Flutter analyze and the debug APK build passed; the full
suite passed 473 tests with one existing skip. The email verification link
report (a “null” result) remains under investigation; successful account creation
is not evidence that its redirect or feedback works.
