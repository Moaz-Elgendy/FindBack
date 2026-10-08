# Home and Find backend additions

Existing ItemDetail/list and search responses add `edited` (title or summary override exists), `description_only`, `reprocessing`, and nullable `reprocess_failure`.

`description_only` is true only with explicit metadata-only evidence or known video description provenance, and no transcript, OCR, article text or visual evidence. Absent evidence is not treated as low confidence.

Search adds `matched_terms: string[]`, derived from actual indexed/title/summary/tag/chunk/private-note/private-intent lexical evidence. Semantic-only results return an empty list; do not parse match_reason into terms.

`POST /api/v1/items/{id}/summarize-again` accepts `{ "replace_edits": false }` (body optional) and returns HTTP 202 ItemDetail. Edited items require `replace_edits: true`, otherwise HTTP 409. Non-owner or deleted ids return 404. Existing in-flight processing returns 409; an already requested explicit reprocess returns its current item. A durable outbox job is committed before queue publication.

The previous brief is durably retained under migration `0017_reprocess_snapshot`, shown while reprocessing (`status=ready`, `reprocessing=true`, so Find retains the previous result), and restored if any stage fails. Generated changes, including vectors/chunks, are committed atomically for explicit reprocessing. Overrides are removed only after a successful confirmed replacement. Failed replacement returns a ready card and a safe `reprocess_failure` message, with old edits and brief retained. Automatic queue dispatch, provenance recovery, retry scheduling and cache destination reuse skip edited items.

Soft deletion restore/purge now use 30 days server-side; restore is allowed strictly before the boundary and purge is eligible at or after it. Visible client Undo timing remains independent. Permanent DELETE without `undoable=true` retains the existing immediate-delete behavior.

No Collections endpoints/services, reminders, notification payloads or infrastructure are added by these prerequisites.
