# Phase 6: UI polish and final verification

Verified on 2026-10-07. This phase preserves Flutter, the existing blue Material 3 theme, navigation, stored content, and existing backend processing.

## Changes in this phase

- Library cards use a quiet outlined surface and give the short Brief more readable type, spacing, and room. Titles, source domains, and tap behavior remain.
- Detail titles and Brief text have clearer typography. Full Brief keeps its prominent blue surface, larger touch area, point count, and source references. Dividers separate its key points. Its hint switches between “Tap to expand” and “Tap to collapse.”
- Tags remain collapsed and are described as search tags. The floating Open Original action remains available while scrolling.
- The topic/filter row supports 48px touch targets and large text. The global offline message wraps on narrow screens; clear search has an accessibility tooltip.
- The save sheet scrolls above the keyboard, including at double text size.
- A nonempty stored LLM Brief stops the generating indicator and polling even while later processing continues, and remains visible without a false “no summary yet” message if a later stage fails. A fallback Brief does not count as final.
- Regression testing caught an expansion hint rebuild error while processing completed. The hint now updates after the frame; the existing polling/refresh test passes.

## Files changed

Product files:

- `mobile/lib/features/home/widgets/result_card.dart`
- `mobile/lib/features/home/detail_page.dart`
- `mobile/lib/features/home/home_screen.dart`
- `mobile/lib/features/home/capture_sheet.dart`
- `mobile/lib/models/item.dart`

Tests:

- `mobile/test/brief_detail_test.dart`
- `mobile/test/brief_ui_test.dart`
- `mobile/test/library_ui_test.dart`
- `mobile/test/multiple_links_test.dart`
- `mobile/test/result_card_test.dart`

This report and the two Flutter render previews below are also added. No backend source, API, schema, worker, or queue behavior was changed in this phase. No migrations were performed.

## Automated acceptance checks

| Requirement | Observed verification |
| --- | --- |
| Brief first; no unused left icon or tag clutter on cards | Card/widget tests pass, including dark mode and 320px width with double text size. |
| No internal match/offline diagnostics on cards | Online and offline card tests pass. The source domain remains. |
| Full Brief visible and easy to open | Point-count/expand hint tests pass; opening exposes points and their source references; the hint changes to collapse. |
| Tags hidden until expanded | Detail tests pass. |
| No “Memory” heading | Detail test passes. |
| Open Original stays on screen | Detail tests confirm the floating action remains hittable after scrolling and with enlarged text. No external browser was launched. |
| All saved items accessible beyond the first twenty | Existing library test loads 45 items through cursors 20 and 40, reaches item 44, and removes Load more at the end. This uses test data, not 45 records in the live database. |
| Duplicate highlighted centrally, with neutral feedback | Existing duplicate tests pass, including a duplicate outside the currently loaded page and Android-share feedback. |
| Batch sharing and pasting | Existing twenty-link/restart/sync tests pass. The new narrow-screen keyboard test saves multiple links through the normal queue path. No new live batch was sent in this phase. |
| Generating stops when an LLM Brief exists | New tests verify items marked processing or failed display their stored LLM Brief, show no generating or false no-summary message, and do not poll for 30 seconds. Existing fallback-to-LLM and media-retry tests pass. |
| Extraction limitations stay in Full Brief | Existing detail test confirms missing-caption text is absent from the short Brief and visible after Full Brief is opened. |

## Commands and results

Run from `mobile/` unless noted:

- `flutter test test/brief_detail_test.dart test/library_ui_test.dart --reporter expanded`: the initial regression tests failed for the generating label and 32px filter targets; these were corrected in product code.
- `flutter test test/multiple_links_test.dart test/result_card_test.dart test/brief_ui_test.dart test/brief_detail_test.dart test/library_ui_test.dart --reporter expanded`: **25 passed**.
- `flutter test test/brief_detail_test.dart test/brief_ui_test.dart --reporter expanded`: **7 passed** after the expansion-refresh correction. A subsequent completed-Brief/failed-stage regression failed for the incorrect no-summary message and was corrected in product code.
- `flutter test test/brief_detail_test.dart test/brief_ui_test.dart .dart_tool/final_ui_probe_test.dart --reporter expanded`: final focused/render run **9 passed** (8 interaction tests and 1 render check).
- `flutter test --reporter expanded --concurrency=1`: final run **115 passed, 1 skipped**.
- `flutter analyze`: final run **No issues found**.
- `flutter build apk --debug --dart-define=API_BASE_URL=http://127.0.0.1:8000`: final build **Built app-debug.apk**.
- `git -c core.whitespace=cr-at-eol diff --check`, from the repository root: no errors.

The existing platform-only skip remains. Backend tests: **NOT RUN** in this phase; backend source was unchanged.

## Rendered previews

A temporary ignored Flutter test rendered the production DetailPage using the actual stored reel returned by the running API, with the existing blue theme and Roboto/Material Icons fonts. The render check passed (**1 passed**), and both images were inspected. These are Flutter widget renders at 412px width, not screenshots from the physical phone. The temporary renderer is not shipped as product code or a permanent test.

- [Light mode: short Brief and collapsed details](ui-verification/phase6-detail-light.png)
- [Dark mode: expanded Full Brief](ui-verification/phase6-detail-dark.png)

## Runtime verification

- API health returned **HTTP 200**; api, outbox, postgres, redis, and worker were running. The item-list and item-detail reads returned the stored reel.
- Live topic filtering returned **1 item** for its stored topic “Claude Code skills,” and **0 items** for an unrepresented topic. These were read-only API checks.
- The saved Facebook reel `https://www.facebook.com/share/r/19PXq2Y3AR/` reported **ready**, **brief_source=llm**, and **prompt_version=brief_v3.3** through the running API. This phase did not generate or manually edit its Brief.
- Samsung SM_S731B was connected over Wi-Fi ADB. Port 8000 reverse forwarding was restored.
- `adb install -r mobile/build/app/outputs/flutter-apk/app-debug.apk`: final installation **Success**. The update used `-r`; app storage was not cleared or uninstalled. The existing screen was not taken over.

## Limits and deferred observations

- Physical-phone visual/tap walkthrough: **NOT RUN**. The phone was initially locked, then another app was active. Its current screen was not taken over for UI automation. The production-widget renders and automated UI tests provide the visual/interaction evidence listed above.
- The live backend currently contains the re-saved reel, so the beyond-twenty library behavior was verified with tests. Earlier database history was not restored in this phase.
- Importance and relationship filters remain deferred from Phase 4; this phase adds no new intelligence dimensions.
- Existing Android lifecycle limits on background upload remain. No new background infrastructure was introduced.
