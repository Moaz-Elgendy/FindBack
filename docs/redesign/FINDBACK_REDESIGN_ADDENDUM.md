# FindBack redesign: addendum to `FINDBACK_REDESIGN_BRIEF.md`

Audience: Codex. Read this together with the main brief and `findback-v4.html`.

**Precedence:** this addendum > main brief > `findback-v4.html`. Where this file changes something, apply it. Anything this file does not mention stays as in the main brief.

**Out of scope for now: Collections.** Leave the Collections tab and screen exactly as the main brief describes. Do not add, remove or redesign anything there. The owner is still deciding how to develop it. Do not implement anything from sections 1 to 8 below that touches Collections.

**Same working rules as the main brief:** audit first, no code until the audit is reviewed; if this addendum conflicts with the existing backend, stop and report it; do not invent backend behavior silently. Update your audit table so it also covers the items below.

---

## 1. Notifications (replaces the Notifications section of the main brief)

1. **Lock-screen text never contains a memory title, for either notification.**
   - Weekly note: title "3 things you saved and forgot", body "Open FindBack to take another look." (Use the real count; singular and plural forms.)
   - Reminder: title "You asked to be reminded", body "A video you saved 2 days ago" (type and age only, e.g. "A recipe you saved last week").
   - Android: set notification visibility to private. iOS: do not put titles in the payload body. Titles may be fetched only after the user opens the app.
2. **The weekly note is skipped when there is nothing to show.** If the count is 0, send nothing. Define "forgotten" in the backend and document it in the audit: for example, saved more than 7 days ago and never opened. Report what the current data model can support.
3. **Tap targets (deep links):**
   - Reminder: opens that memory's detail screen.
   - Weekly note: opens a new simple screen, **"Worth another look"**: a plain list of those same items as standard cards, with a back button. No filters, no extra chrome. The list must match what the count said when the note was sent (compute it server-side or snapshot the ids).
4. **Permission:** ask for notification permission at the moment the user first sets a reminder or turns on the weekly note, never at launch. Show one line of context before the system prompt ("We'll send one notification at the time you choose."). If permission is denied, do not set the reminder silently: show a toast "Notifications are off. Turn them on in Settings to get this reminder" with an Open settings action. Keep the reminder stored, and have it start working if permission is granted later.

## 2. Find mode and search

1. **Matched-terms line only when real.** Show "Matched: ai, presentations" only when the backend returns actual matched terms for that result. For semantic-only hits, show nothing, or a neutral "Similar to what you described". Never show an empty or incorrect "Matched:" line.
2. **Filter chips are generated, not hardcoded:**
   - Always: type chips (a video, a recipe, something to buy) and the time chip (last 2 weeks).
   - Plus up to two topic chips from the user's own most-used categories (e.g. "about AI" appears only if the user has AI saves).
   - Hide any chip that would give zero results on its own.
   - Maximum 6 chips; they wrap, never scroll sideways.
3. The text box gets keyboard focus immediately when Find mode opens.
4. Empty states stay as in the main brief. Add one for a brand-new account: see 3.1.

## 3. Home states that were missing

1. **Empty Library (new account, 0 saves).** A calm centered panel: title "Save your first link", body "Share any link to FindBack from another app, or paste one here.", primary button "Save a link" (opens the Save sheet) and a short 3-step visual: Share, Read, Find. No marketing, no carousel, no skip button needed. It disappears after the first save.
2. **Feedback after sharing from another app (share extension / intent).** Show a short confirmation inside the share UI or on return: "Saved. Reading it now." The new item must then appear at the top of Library as the "Just saved" card.
3. **Offline / queued.** If a save happens without a connection, store it on the device and show it in the feed as a card: "Saved on this phone. Will be read when you're back online." The header pill counts it ("Reading N" includes queued items). When the connection returns, process automatically.
4. **Duplicate link.** If the link is already saved, do not create a second item. Show a toast "Already saved" with a View action that opens the existing memory.
5. **Low-confidence brief.** When the pipeline could only use the page title or description (no transcript, no article text, login or paywall limited), mark the brief with a small line under the title: "Based on the page description only". Needs a flag from the backend (see section 8). The brief is still shown; the line is just honest. Do not guess missing content.

## 4. Memory detail and edit

1. The ⋯ menu gets a third item: **"Summarize again"**, placed between Edit and Delete (Edit, Summarize again, Delete). Behavior: re-runs processing; the card shows the "Just saved · reading it now" style indicator while it runs; if it fails, the old brief is kept and the failed-link message is shown in a toast, not by replacing the card.
2. **User edits are never overwritten.** If the user edited the title or brief, "Summarize again" asks first: "Replace your edits with a new summary?" with Cancel and Replace. Automatic reprocessing (retries, pipeline upgrades) must never touch an edited item. Needs an `edited` marker (see section 8).
3. The long-press / ⋯ menu on Home also gets "Summarize again" in the same position.
4. **Undo toast timing.** Keep the 5 seconds for sighted users, but when a screen reader (TalkBack / VoiceOver) is on, keep the toast until dismissed or for 10 seconds, and announce it ("Memory deleted. Undo"). Soft-delete purge delay must be at least 24 hours; recommended 30 days.

## 5. Reminders: additions to section 5 of the main brief

1. **Late-night rule.** Between 00:00 and 04:59 local time, "Tomorrow, 9:00 am" is replaced by "Today, 9:00 am" (the user is still in "tonight"). The "This evening, 7:00 pm" option also appears in that window. Document the rule in code and tests.
2. Slot computation must use the device's current time zone and handle DST changes. The stored value stays an absolute UTC timestamp plus the IANA time zone name.
3. If a reminder time passes while the device is off, deliver it when the device is back on (do not drop it). If it is more than 24 hours late, still deliver, with the same generic body.
4. Permission handling: see section 1.4.

## 6. Color and theme

1. **New control-border token.** Cards keep the soft `line` token. Interactive controls (search box, text inputs, unselected filter chips, outlined buttons) use a stronger `control-line` so they reach 3:1 against their surface.
   - Light: `#6F918C` (3.15:1 on bg, 3.44:1 on white).
   - Dark: `#4F7A74` (3.81:1 on bg, 3.43:1 on card).
   - Verify every control in both themes with a contrast check; adjust the hex only if a surface differs from the ones above.
2. **Amber has one job family: in-progress and user-chosen.** Remove amber from the Recipe type chip. New Recipe chip colors (both pass 4.5:1): light background `#FBE4EA`, text `#8A1F3D`; dark background `#3A1A24`, text `#F5A3B8`. Video stays teal, Product stays blue, Recipe is now rose. Amber remains only for: "Reading N" pill, "Just saved" card, active reminder button.
3. **Theme default is Auto** (follow the OS) for new installs, instead of Light. The Appearance row in Account stays Light | Dark | Auto. If the owner prefers Light as the default, it is a one-line change; mention this in your notes.
4. Dark mode is part of the definition of done for every new component in this addendum (empty state, offline card, low-confidence line, "Worth another look" screen).

## 7. Accessibility and platform translation (the main brief is written like a web project)

The mobile app is Flutter. Translate web terms as follows and do not implement web-only behavior:

| Main brief says | Implement as |
|---|---|
| `prefers-reduced-motion` | `MediaQuery.disableAnimations` / platform reduce-motion: stop the shimmer and the pulsing dot, replace with static states |
| Theme "Auto" follows the OS | `ThemeMode.system` |
| 360px and 390px widths | 360dp and 390dp logical widths; also check a small phone (~320dp) |
| Right-click, Space key | Not needed on phones. Keep long-press and the ⋯ button |
| Focus visible | Keep for hardware keyboards and switch access |

Additional requirements:
1. **Screen-reader parity for long-press.** Cards expose Edit, Summarize again and Delete as custom accessibility actions (`Semantics.customSemanticsActions`), in addition to the ⋯ button.
2. **Large text.** Test every screen at system text scale 1.0, 1.3 and 2.0. Nothing may clip or overlap: header pills wrap or shrink to an icon with count, chips wrap, bottom bar labels stay visible, tap targets stay at least 44dp.
3. **Right-to-left and Arabic content.** Do not build full Arabic UI in this pass, but do not block it: use directional padding/alignment (`EdgeInsetsDirectional`, `AlignmentDirectional`, `TextAlign.start`) in all new widgets. Summary text may contain Arabic or other non-Latin scripts: make sure the serif and sans font stacks have a fallback that renders Arabic (e.g. a bundled Arabic-capable font) and that mixed-direction lines render correctly. Report the current state of this in your audit.
4. All icon-only buttons have semantic labels. The centered Save button is labeled "Save a link".

## 8. Backend additions to report on (verify against the current API; do not invent silently)

Add these to the list in section 7 of the main brief:

11. A "forgotten" definition and a way to list the items counted in the weekly note (section 1.2, 1.3).
12. Per-item flag for **content source quality**: whether the brief used full content or only the page description (section 3.5).
13. Per-item `edited` marker covering title and brief; reprocessing paths must respect it (section 4.2).
14. A "Summarize again" endpoint, plus behavior on failure that keeps the previous brief (section 4.1).
15. Duplicate detection by normalized URL, returning the existing item id (section 3.4).
16. Search results that include matched terms only when truly lexical (section 2.1), and a way to get the user's top categories for generated chips (section 2.2).
17. Notification payloads without titles (section 1.1) and a snapshot or recompute of the weekly-note item ids (section 1.3).

## 9. Tests to add (on top of the main brief's list)

1. Reminder slots: 00:30 (late-night rule), 18:30 ("This evening, 7:00 pm" hidden because it is under an hour away) and 17:30 (shown), Saturday and Sunday, DST change day, and device time zone different from the stored one.
2. Weekly note: count 0 sends nothing; count N sends a title-free notification; tap opens "Worth another look" with the same N items.
3. Notification payloads and local notification content contain no memory title (both types).
4. Permission denied path: reminder stays stored and a toast with an Open settings action is shown.
5. Duplicate save returns the existing item and shows "Already saved".
6. Edited item is not changed by automatic reprocessing; "Summarize again" asks before replacing edits.
7. Generated filter chips: hidden when empty, capped at 6, topic chips come from the user's data.
8. Large text (2.0) golden or widget test for Home header, Find chips and bottom bar.
9. Contrast test for the new `control-line` and Recipe chip tokens in both themes.

## 10. Updated phase plan

Keep the main brief's phases (a) to (f) and fold these in:
- (a) tokens: add `control-line`, new Recipe chip colors, Auto default.
- (b) components: add the "Summarize again" menu item, the offline card, the empty-library panel, the low-confidence line.
- (c) Home and Find: sections 2 and 3.
- (d) Memory detail and reminders: sections 4 and 5.
- (e) Account (Collections untouched).
- (f) Notifications: section 1 and the "Worth another look" screen.

## 11. Open decisions: use these defaults, note them in your report, and do not block on them

- Theme default: Auto (section 6.3).
- "Forgotten" threshold: 7 days and never opened.
- Soft-delete purge window: 30 days.
- "Worth another look" screen: simple list only.

## 12. Definition of done (addition)

Everything in the main brief's definition of done, plus: dark mode and 2.0 text scale checked for every item above, no memory title in any notification, and a short note listing anything you did differently and why.
