# Account contracts — phase (e)

All routes use the existing bearer authentication and act only on the caller. Collections UI and grouping are unchanged. Weekly notification delivery belongs to phase (f).

## Weekly note settings

`GET /api/v1/account/weekly-note` returns defaults when no preference has been stored:

```json
{"enabled": false, "weekday": 6, "hour": 18, "minute": 0, "time_zone": "UTC"}
```

`PUT /api/v1/account/weekly-note` takes the same object and returns the saved choice. Weekdays are Monday=0 through Sunday=6. Hour/minute are local wall time; `time_zone` must be a valid IANA name. The app saves the device zone when enabling or choosing a schedule. The preference is owner-scoped. Notifications are requested only after contextual opt-in; a denied permission keeps the preference and offers Open settings. There is no permission prompt at launch. Guests are invited to sign in for the weekly note.

## Export

`GET /api/v1/account/export` returns a version-1 JSON document with `exported_at` and `saves`. Each active save contains id, source URL, edited title/summary when present, saved date, type, tags, structured brief fields and the owner's note/intent. Soft-deleted saves, raw fetched content, embeddings and authentication secrets are excluded.

Flutter adds locally queued saves to signed-in exports, deduplicating URLs. Guest exports contain that device's library. The existing native share sheet receives `findback-saves.json`. The initiating identity and storage scope are checked before merging or sharing; switching accounts aborts the export. Export requires a successful server read for signed-in users, so it cannot silently produce a partial server archive offline.

## Account deletion

`DELETE /api/v1/account` returns 204 only after deleting the authentication identity and application data. The Flutter UI asks for permanent-deletion confirmation, then stops account work, cancels alarms, clears the account's device cache/queue and signs out. Provider failure leaves the application account and saves in place. Another account's cache is preserved.

The backend uses `SUPABASE_URL` plus backend-only `SUPABASE_SERVICE_ROLE_KEY`. The user's issuer must match the configured Supabase project and its subject must be a UUID. Unsupported or unconfigured identity deletion returns 503; provider failure returns 502. Redirects are disabled. Provider 404 is accepted for retry after an already completed identity deletion.

Raw snapshots under the caller's saved item prefixes are removed before provider deletion. Storage writes and deletion take the same owner row lock and uploads recheck that the item exists. This prevents an in-flight fetch from leaving a new raw object after deletion. Storage failures stop deletion. When raw storage is configured, its deletion credentials must remain available. Historical raw objects whose item rows were already permanently purged cannot be discovered by this per-item cleanup.

Items are removed before user memories to respect the existing composite foreign key. Private owned assets and account preferences cascade; public shared assets are detached from legacy ownership and preserved for other users. A SHA-256 subject digest in `deleted_identities` prevents unexpired old JWTs from recreating the account; no email, title, summary or token is stored there. A new signup with a different provider subject can reuse the email.

Provider, object storage and PostgreSQL are independent systems. This is not a distributed transaction: if provider deletion succeeds and the final database commit fails, the original access token can retry until expiry, after which operator cleanup may be required. Raw content may already have been removed when a later provider request fails. Native share artifacts already exported by the user are not recalled by deletion.

## Migration

`0019_account_settings` follows `0018_reminders` and adds `weekly_note_preferences` and `deleted_identities`. SQLite remains version 6; account deletion clears existing tables. No application database was migrated or deployed during implementation.
