# Phase 3 video verification — 2026-10-08

Resumed interrupted video work only. No production migration, deployment, database writes, APK install, or Git push was performed.

## Verified

- tt.site and resolved video URLs reach acquisition (regression tests).
- Navigation-only source text is rejected; supported video-identifier tutorial content remains valid.
- Unknown-duration media is probed and rejected above the configured long-video cap.
- Short portrait video can retain visuals when its minimum resolution exceeds 480 pixels. Live supplied TikTok (`https://www.tt.site/t/ZSbbndnpn/`) resolves to video 7678469739454254357; its extractor reports five seconds. The fixed selector downloaded MP4 and extracted one frame.
- Visual observations cannot confirm guessed tool names. Frame count and payload size are bounded.
- The vision helper reuses the configured Gemini model by default. Previous hardcoded gemini-2.5-flash returned HTTP 404. With current configuration, Gemini returned HTTP 429 after three attempts, then CapacityPause. No successful visual response was obtained.
- A navigation-only cached guest brief is no longer final and restarts through the existing guest ingest path when its server lease is absent. Regression preserves the URL. This was not installed/tested on the phone.

## Exact checks

```sh
SUPABASE_URL='' TEST_DATABASE_URL=postgresql://findback:findback@localhost:55433/findback_test REDIS_URL=redis://localhost:6379/0 backend/.venv/bin/python -m pytest -q backend/tests/test_video_aliases.py backend/tests/test_scene_evidence.py backend/tests/test_media_understanding.py backend/tests/test_brief_integrity.py backend/tests/test_brief_segment_ids.py backend/tests/test_fetcher_reader.py backend/tests/test_video_input.py
```

Result: `66 passed in 0.33s`.

From mobile:

```sh
flutter test test/guest_library_test.dart test/items_service_test.dart
flutter analyze lib/models/item.dart test/guest_library_test.dart
```

Results: `All tests passed!` (16 tests), `No issues found! (ran in 2.0s)`.

```sh
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check
```

Result: exit 0. Existing CRLF file endings preserved.

## Real provider evaluations and limits

Two operator eval runs used `scripts/eval_brief.py --url https://www.tt.site/t/ZSbbndnpn/` inside the worker image with current source mounted. Artifacts: `/tmp/findback-tiktok-live-resume.json` and `/tmp/findback-tiktok-live-fixed.json`. Both returned real Groq briefs after Gemini quota failures; both eval cases in each file passed the existing checks. These artifacts predate the final portrait-format fix and do not prove visual analysis. First run included one unsupported-name rejection and a successful repair. First full acquisition used local base STT but audio-only media; second used cloud STT with no configured credentials and audio-only media. Neither establishes accurate scene understanding.

Caption-only evaluation incorrectly described a TikTok Shop announcement from reader content, which disagrees with the video extractor caption. Existing eval checks did not detect that disagreement. The acquired caption is `#fyp #foryou الحمدالله 😂`; no exact skill names are established. These evaluation results were not persisted to a saved production record.

Final live visual check extracted a real frame with fixed selection and called the configured Gemini model. Result: repeated 429 and `capacity pause Visual provider temporarily unavailable`. Accurate real visual brief and phone repair remain unverified. Do not describe this phase as a complete production-quality fix.

Deferred: investigate reader/source identity mismatch and strengthen factual eval; then rerun with available visual quota. Collections, full security/release audit, production migration/deployment, and device tests remain outside this single resumed phase.
