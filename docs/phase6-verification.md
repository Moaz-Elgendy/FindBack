# Phase 6 verification

Final verification is in progress. This feature branch has not been merged or deployed.
Phase 5 commit `8c9930d` passed CI38039569558 (1131 backend / 463 Flutter tests,
one existing skip each). Phase 6's final CI gate is pending.

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

- Full Flutter: 468 passed, one existing skip. Final rendered/widget matrix: 32 passed.
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
No FindBack database or account was cleared. A reliable fresh-save/recovery run
through the final configured APK has not been completed.

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
an Apple signing team. Live email/auth, notification delivery and verified
production App Links are untested in this phase.

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
