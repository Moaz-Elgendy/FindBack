# Item actions API prerequisite

Apply migration `0016_item_actions` and update API, dispatcher, and workers before
the redesigned mobile components use these contracts. Authentication and ownership
checks are the same as existing item reads. Missing, deleted, or another user's
items return 404.

| Request | Body | Success |
| --- | --- | --- |
| `PATCH /api/v1/items/{id}` | `title` and/or `summary` | 200, updated ItemDetail |
| `POST /api/v1/items/{id}/retry` | None | 202, ItemDetail |
| `POST /api/v1/items/{id}/keep-link` | None | 200, ItemDetail |

`summary` is the editable Brief. Title must contain non-whitespace text and is
limited to 500 characters; Brief is limited to 20,000 characters and may be empty.
Null, unknown fields, and empty edits return 422. Omitted fields retain their
values. Edits remain private to this save and survive later processing. Item
reads, library lists, and search results return the edited text. Lexical search
includes edits; existing semantic vectors continue describing source content.

ItemDetail now includes `failure_reason` and `link_only`. Failure reasons are
safe display messages, never raw worker exceptions. Queued work uses the existing
`pending` status, active work uses `processing`, and failures use `failed`.
Existing degraded briefs retain `ready` with `needs_retry=true`.

Retry accepts failed or degraded saves. Already pending/processing saves return
202 without duplicating work. A durable job is committed before publishing;
Redis outages leave it pending for the existing dispatcher. Retry does not count
as another save. Successfully read or link-only saves return 409.

Keep link only accepts failed or degraded saves and is idempotent. It retains the
URL and title, hides generated summaries/points, and disables future processing
of that save. User edits remain visible. Another user's processing is preserved.
Both failure actions return 409 while a worker actively owns the shared job;
the client should offer the action again after processing finishes.

Migration rollback preserves edited Title/Brief in legacy columns, but older
clients/workers cannot enforce the link-only choice. Coordinate rollback of all
services. No production migration or deployment was performed during this phase.
