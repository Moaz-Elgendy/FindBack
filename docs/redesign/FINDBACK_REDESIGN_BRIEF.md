# FindBack UI/UX redesign: handoff brief

Audience: Codex. You built the current app and know its backend and code. This brief tells you what the new UI/UX must be and what it needs from the backend. Where this brief and `findback-v4.html` disagree, **this brief wins**. Where this brief and the existing backend disagree, **stop and report it** (see "How to work").

## 1. What the HTML file is, and is not
`findback-v4.html` is a **visual and behavioral reference**: a static prototype with fake data, hardcoded phone frames and vanilla JS. Open it in a browser and use it to see layout, spacing, states and interactions.
- Do **not** copy its code, structure or CSS architecture. Rebuild every screen with our existing stack and component conventions.
- Do **not** copy the fake data, the 390x800 phone frames, or the page-level theme button (that button exists only for the prototype; the real toggle is the Appearance row in Account).
- Use it for: the design tokens in section 3, the visual look of each component, and how each interaction feels.

## 2. Product idea (keep every decision consistent with it)
FindBack saves links, reads and summarizes them, and lets you find them later **by describing what you remember** ("a video about some AI tool that makes presentations"). You should not have to organize anything. Notifications are rare: one weekly note, plus reminders only when the user asks.

## 3. Design tokens
Light: bg #EDF7F5, card #FFFFFF, ink #0C2A28, mute #4A6664, line #D3E6E3, brand #0F766E, soft #D5F0EC, danger #B42318, accent (amber) #E8A317, accent-soft #FFF0D2, accent-ink #8A5200, on-brand #FFFFFF.
Dark: bg #0A1715, card #112220, ink #E4F1EF, mute #9CB8B4, line #223C39, brand #35BFB1, soft #14403B, accent-soft #3A2B0E, accent-ink #F6C768, on-brand #04201D, danger text #FF8A7D.
Other tokens (toast, pale card tints, product-chip colors, collection stack colors) are in the `:root` and `[data-theme=dark]` blocks of the HTML.
Rules: amber is only for in-progress things and things the user chose (Reading indicator, "Just saved" card, active reminder, recipe tag). Teal is brand/actions/selected. Never use color alone to carry meaning.
Type: Schibsted Grotesk for UI; Source Serif 4 for summary content (bullets, notes). Body 15px; nothing smaller than 12.5px; muted text must keep at least 4.5:1 contrast in both themes.
Shape: cards 22px radius, buttons/inputs 14-16px, pills fully round. Tap targets at least 44px.
Theme: Light / Dark / Auto (Auto follows the OS). Stored on the device, not on the backend. Light is the default for new installs.

## 4. Screens and behavior

**Navigation:** bottom bar with Library, a centered round Save button, and Collections. Account is the top-right avatar.

**Home (Library)**
- Header: "FindBack", an optional single amber pill "Reading N" (N = items being processed or queued; hidden when 0), and the avatar.
- Search box "What do you remember?" opens Find mode (full-screen).
- Feed of cards, newest first. Card = type chip (Video/Recipe/Product plus category, colored by type), age ("2 days ago"), a ⋯ button, title, then up to 3 summary lines (serif) with "+N more" / "Show less". Recipes/products show 2-3 key figures plus one note.
- Special cards at the top: **Just saved · reading it now** (amber dashed, shimmer, while processing) and **Couldn't read this page** (reason shown, e.g. paywalled; actions Retry and Keep link only).
- ⋯ or long-press (or right-click, or Space on keyboard) opens a small menu: Edit, Delete.
- Delete is immediate with a toast "Memory deleted · Undo" (5 seconds). No confirmation dialog. Backend must support undo (soft delete with a delayed purge).
- Edit opens a bottom sheet with Title and Brief (both editable and saved).
- Tapping a card opens Memory detail.

**Find mode**
- Large text box, placeholder example, a live count ("2 memories found" / "Newest saves first" when empty).
- Filter chips: a video, a recipe, about AI, last 2 weeks, something to buy. Chips combine with the text.
- Results are cards plus a line "Matched: ai, presentations". Empty result: "Nothing matches yet. Try one thing you remember: a topic, a place, a name."
- IMPORTANT: the prototype uses naive keyword matching. The real search should be semantic/fuzzy (it is the core of the product). Keep or improve what the backend already does; the UI only needs ranked results plus matched terms.

**Save a link (sheet)**
- Fields: Link or shared text; "What you remember about it (optional)". Actions: Cancel / Save. Sharing from another app skips the sheet and saves directly.

**Memory detail**
- Back, ⋯ (Edit, Delete). Title, source line (type, site, length, saved when). Numbered skills/points in a white pane with "Jump to 0:42" links (video timestamps).
- **Remind me (new flow, see section 5).**
- Bottom dock: "Open original" (primary) and "Share".

**Collections**
- Automatically grouped (e.g. AI tools, Weeknight cooking, Running) with save counts. Subtitle: "Grouped for you automatically. Nothing to organize." No manual "New collection" button in this version.

**Account**
- Signed-in email + Sign out.
- Weekly note: toggle "Remind me of forgotten saves" (one notification a week, never daily) + day-time row ("Sunday at 6:00 pm").
- Appearance: Light | Dark | Auto.
- Your data: Export my saves; Delete account (removes saves and summaries).
- Note: links are read and summarized by an AI service; nothing is shared with other people.
- The old "When a reminder fires" group is **removed**.

**Notifications**
- Weekly note: "3 things you saved and forgot" + the first titles.
- Reminder: "You asked to be reminded" + a generic body ("A video you saved 2 days ago"). **Memory titles must not appear on the lock screen.**

## 5. Reminders (final decision: one button, real times)
- One button on Memory detail: "Remind me". It opens a bottom sheet with options computed from the device's current time and time zone, each showing its real time:
  - This evening, 7:00 pm (shown only if more than 1 hour away)
  - Tomorrow, 9:00 am
  - Saturday, 10:00 am (label becomes "Next Saturday" when today is Saturday)
  - Pick date and time (native date-time picker; past times are rejected)
- Choosing an option sets the reminder immediately, closes the sheet and shows a toast "Reminder set". The button turns amber and reads "Reminder · Tomorrow, 9:00 am".
- Tapping the button again reopens the sheet with an extra "Remove reminder" row.
- One reminder per memory. The backend stores an **absolute timestamp** (UTC) plus the user's time zone; there are no per-user default times and no preset names.
- Reminders default to off.

## 6. Copy
Sentence case. Same verb through a whole flow ("Delete" then "Memory deleted"). No apologies in errors; say what happened and what to do. Take all strings from the HTML as the starting copy deck, then fit them to our i18n setup if we have one.

## 7. Backend implications (verify each against the current API)
1. Item processing state: queued / processing / failed (with a human-readable reason) / ready. Retry action. "Keep link only" mode.
2. Search endpoint returning ranked results plus the matched terms; date filter (last 2 weeks) and type filters.
3. Categories and types per item (Video, Recipe, Product, plus a category name). Product/recipe key figures and a note.
4. Video summaries with timestamps for "Jump to".
5. Automatic collections and counts.
6. Edit title and brief. Soft delete with Undo (restore endpoint) and delayed purge.
7. Reminders: set, change, remove (one per memory) and push delivery with the generic-body rule.
8. Weekly note: enabled flag, weekday, time, send logic ("3 things you saved and forgot").
9. Export all saves; delete account and data.
10. Auth session info (email) and sign out.
Anything missing from this list that the UI needs: report it, don't invent it silently.

## 8. How to work
1. **Audit first, no code yet.** Read the current frontend and API. Produce a table: screen -> current implementation -> needed change -> backend gap. Include the list of components you will create or reuse. Post it for review.
2. Then implement in phases, each shippable on its own: (a) design tokens + theming (Light/Dark/Auto); (b) shared components (cards, chips, sheets, toast with Undo, buttons); (c) Home + Find; (d) Memory detail + reminders; (e) Collections + Account; (f) notifications.
3. Keep the existing routing, state management, API client and tests. Do not rewrite what isn't visually changing.
4. Accessibility floor: keyboard focus visible, 44px tap targets, `prefers-reduced-motion` respected (the shimmer and pulsing dot stop), labels on icon-only buttons, both themes pass contrast.
5. Add tests for: reminder slot logic (including Saturday, late-evening and Sunday cases), undo delete, theme persistence, and the failed-link card.
6. Definition of done: each screen matches the HTML in both themes at 360px and 390px widths; no regression in existing flows; a short note listing anything you deliberately did differently and why.
