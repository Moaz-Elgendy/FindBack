# Phase (e): Account inspection and proposed scope

Collections UI remains untouched under the redesign addendum. The user approved the proposed backend work by asking to continue after this inspection report.

## Audit

| Requirement | Current implementation | Needed change | Backend gap |
|---|---|---|---|
| Signed-in email and Sign out | Account page shows email and Log out; existing authentication handles signup, reset and recovery | Restyle Account using existing design tokens; retain authentication flows | None |
| Appearance: Light, Dark, Auto | Existing persistent AppearanceScope and segmented control | Account settings layout with large-text and RTL support | None |
| Weekly note and Sunday 6:00 pm row | No preference model or API | Persist enabled state, weekday, local time and IANA zone; contextual permission flow | No weekly preference contract; delivery belongs to phase (f) |
| Export my saves | No export operation | Owner-scoped export including the user's edited titles and summaries; share the resulting artifact | Export format and API absent |
| Delete account | Sign out only | Explicit destructive confirmation, server removal and local account cleanup | No account deletion API or external authentication identity deletion flow |
| AI privacy note | Not present on Account | Add brief disclosure from the brief | None |

## Proposed implementation plan

- Files: `mobile/lib/features/account/account_page.dart`, existing `mobile/lib/data/api_client.dart`, `mobile/lib/services/auth_service.dart`, Account service wiring and focused tests; new backend Account router, schemas, preference migration and tests if the missing backend scope is approved.
- Database: weekly preference storage only in this phase. Weekly snapshots and opened-item tracking remain phase (f). Account deletion must enumerate owner data and preserve other users' shared content.
- API: define authenticated weekly preference read/update, save export and account deletion contracts before implementation. Deletion must cover the authentication provider identity, not merely the application user row.
- Risks: irreversible account deletion; remote authentication deletion and database cleanup cannot be one transaction; cross-account local state and scheduled reminders must be cleared correctly; shared content must not be deleted on behalf of another user.
- Tests: retain sign-in/signup/reset/recovery/sign-out coverage; test settings persistence and denied permissions; verify export ownership and edited content; test deletion ownership, external-provider failure and retry behavior; check light/dark, 320/360/390dp, text scales 1/1.3/2 and RTL.

## Blocker

The initial inspection stopped because the weekly preference, export and account deletion contracts did not exist. The subsequent user instruction to continue approved implementing the proposed scope. See `ACCOUNT_API_CONTRACT.md` for the implemented contracts and failure behavior.

Implementation uses the existing authentication, scoped services, API client, theme, notification permission port and native share plugin. No new dependency or infrastructure was added. Weekly delivery remains phase (f).
