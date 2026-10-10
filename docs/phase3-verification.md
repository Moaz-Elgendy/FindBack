# Phase 3: Flutter foundations

The application uses the supplied v6 palettes in both themes, including the navy dark background, amber recipe badges, and shared dialog/menu/toast surfaces. Prototype page/bezel colors belong to the HTML presentation frame rather than application screens.

Library and Collections refresh silently on app resume; Library also retains its existing processing timer and sync notifications. Pull-to-refresh controls and the refresh hint are removed. A queued card says “Saved on this phone · waiting to upload” without claiming that the device is offline.

Memory deletion uses one confirmation with “Don't show this again”. Only confirming a deletion saves the opt-out. Account's “Show delete confirmations” resets it. The preference belongs to the account's local database; account deletion remains separately confirmed. SQLite version 8 adds the preferences table and preserves queued saves and memories on upgrades.

Feed requests reuse the server ETag only for the same authorization and exact request URL. A 304 retains the previously fetched body; account or filter changes cannot reuse another account's response.

Regression coverage includes palette constants, SQLite 7→8 preference persistence/reset and existing 4→8 data preservation, ETag/account/query separation, anchored menu dismissal at narrow width, confirmation/cancel/opt-out/reset, deletion/undo and reminders, empty-library and filter refresh on resume, queued save recovery, Collections resume refresh, and the Account reset row.

Device visuals and manual phone checks remain for Phase 6. Production deployment is unchanged.

Local verification: `flutter analyze` clean; `flutter test` 447 passed, one existing skip. Fresh review found no important correctness regressions.
