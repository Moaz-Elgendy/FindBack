# FindBack — Product Requirements Document (PRD)

**Version:** 1.0 — MVP  
**Tagline:** *"You don't need to remember where you saved it. Just remember what you remember about it."*  
**Positioning:** Personal memory for the internet — not a bookmark manager.

## 1. Vision & Principles

FindBack replaces scattered saves (IG Saved, YT Watch Later, Bookmarks) with a single associative memory. AI understands topic, entities, intent and context.

**Principles:**
1. Invisible Organization — Zero folders, AI categorizes in background.
2. Sub-Second Trust — Save <1.5s acknowledge, Find <500ms.
3. Respect for Attention — No feed, no social. In-and-out in 10-15s. Save → Find → Use.

## 2. Goals & Non-Goals

**MVP Goals:** Save any link/video/article/recipe/product in one tap via Share Sheet (incl. offline); retrieve with vague natural language sharing zero keywords with title; offline saves never lost.

**Non-Goals (MVP):** Manual folders, social/public collections, collaboration, browser extension, desktop app.

**Anti-Goals:** No inbox requiring curation; no mandatory onboarding — first Save *is* onboarding.

## 3. Personas

- **The Learner (Primary):** Tutorials, threads, AI tools. Query: "that AI presentation tool" / "AWS crash fix"
- **The Foodie:** Recipes from Reels/TikTok/blogs. Query: "chicken cream mushroom"
- **The Shopper/Researcher:** Products/comparisons. Query: "headphones noise canceling under 200"

## 4. User Stories (MVP)

**Save:**
- Share from YouTube → see `Saved ✓` in <1s, keep watching.
- Share offline on subway → `Saved offline — will sync when online`; appears when online.
- Saving same URL twice deduplicates, bumps last_seen.

**Find:**
- Type vague gist ("that AWS tutorial about fixing crashed applications") → correct item in top 3, <500ms.
- See *why* it matched ("Matched: creamy mushroom chicken") to build trust.
- Offline: lite search over cached titles/tags with banner.

**Use:**
- Recipe: Ingredients + Steps + Cook Mode (keep screen awake).
- Tutorial: TL;DR + Key Points + Open Original.
- Actions: Open Original, Copy Summary, Delete.

## 5. UX Spec

**Home = Search.** No tabs. Search bar auto-focused on launch, keyboard up. Filter chips: `All | Recipes | Tutorials | Products | Tools`.

**Result Card:** Thumbnail left, title (1 line), AI summary (1 line), tags + match reason in faint gray, source favicon + date. Primary CTA `Open`; secondary `Summary`.

**Empty State:** "No exact match — Closest memories:" + 3 nearest + CTA "Search the web?"

**Detail Sheet (Bottom Sheet):** Summary (2 lines), Key Points (3 bullets), Entities (ingredients/tech/people), Tags (read-only), `Open Original` (primary), Copy/Share/Delete.

**First-Run:** 3 example queries + button "Share anything to FindBack to start". After first save, hint "Try searching what you remember...".

## 6. Functional Requirements

| ID | Req | Acceptance |
|----|-----|------------|
| F1 | Share Sheet | iOS Share Extension + Android ACTION_SEND; text/plain, text/uri; <15MB; writes to App Groups + SQLite queue; Saved ✓ <1.5s |
| F2 | Content Fetch | YouTube transcript + Whisper fallback, articles via Firecrawl/Jina, TikTok/IG via oEmbed+Apify; raw snapshot to R2/S3 |
| F3 | AI Understand | Single LLM JSON call: summary, key_points[3], category, entities, intent, tags[3], title_clean; temp 0.1; schema validated |
| F4 | Hybrid Search | Vector (cosine) + BM25 via RRF (k=60, w 0.7/0.3); rerank top-20; vague NL queries; filters by category/source/date |
| F5 | Offline Queue | Pending queue survives kill; NetInfo + WorkManager/BGTaskScheduler auto-flush w/ backoff; dedupe by canonical_url |
| F6 | Detail & Action | View summary/key points/entities/tags; Open, Copy, Delete |
| F7 | Auth & Sync | Apple/Google/Email via Supabase/Clerk; JWT; cross-device sync |
| F8 | Dedupe | Same canonical_url per user returns existing item, updates last_seen_at |

## 7. Non-Functional Requirements

- Save acknowledge p95 <1.5s (AI async <15s)
- Search p95 <500ms
- Cold start <1.2s; 10k items/user before degradation; iOS 16+, Android 10+

## 8. Edge Cases

- Blocked scrape (IG/TikTok): save preview text + prompt for 3-word note.
- No YouTube transcript: Whisper via yt-dlp; if fails, store metadata only.
- Long content >2000 tokens: chunk 20% overlap, multi-vector, max-score.
- Duplicate URL globally: reuse embedding/cache.
- LLM invalid JSON: retry once, else mark failed, preserve raw for reprocess.

## 9. Success Metrics

- Activation: First vague search success <60s after first save
- North Star: % users Search within 7 days of Saving >45%
- Search Success: Tap-through top-3 >70%
- Performance: Save/Search p95 targets; Quality: Recall@5 >0.85 on golden set
- Pricing (Post-MVP): Free 150 saves; Pro $6/mo unlimited + OCR/transcripts/import

## 10. Release Plan

- Weeks 1-2: Skeleton (repo, auth, share extension → SQLite, no AI)
- Weeks 3-6: Magic loop (fetcher + LLM + embed + pgvector + hybrid search; golden eval)
- Weeks 7-10: Polish to 15s (thumbnails, chips, detail sheet, offline sync, perf)
- Weeks 11-12: 50-user beta + landing page/waitlist

## 11. Open Questions

iOS-first or both? Screenshot OCR in V1? LLM provider? Is FindBack name/domain final?

