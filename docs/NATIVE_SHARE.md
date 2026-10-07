# Native sharing

FindBack's Android share target is implemented in:

- `mobile/android/app/src/main/AndroidManifest.xml`
- `mobile/android/app/src/main/kotlin/com/findback/findback/MainActivity.kt`
- `mobile/lib/services/share_intent_service.dart`

Installing the APK registers **FindBack** for `ACTION_SEND` and `ACTION_SEND_MULTIPLE` with `text/plain`. Shared text can contain one link or multiple links. The same URL extraction/capture service handles pasted and shared text. Multiple links are deduplicated within the payload and written to the existing SQLite queue before upload. Single-link duplicates use the existing “Already exists” flow.

## Delivery contract

The existing `findback/share` method channel provides:

| Direction | Method | Payload |
|---|---|---|
| Dart → Android | `getInitialShare` | Returns pending shared text once, or null |
| Android → Dart | `onShare` | Shared text after the listener is ready |

Dart subscribes before requesting the initial share. Android buffers the launch intent and any additional shares arriving before that request; the request drains them together. This prevents warm shares during startup from being discarded by the broadcast stream before Dart subscribes. Subsequent shares use the existing live callback.

Sharing uses the existing save/offline queue path. This is not an Android background-service implementation: the app must be running to drain its queue. The queue persists across app restarts, but an Android intent not yet saved to SQLite is not a durable record.

## Finding or pinning FindBack

1. In Facebook or another app, choose **Share** and open the Android/system share sheet. An app's own shortcut row may require **More** or a system-share option first.
2. Select **FindBack**. It may be under the full app list.
3. If the system sheet supports it, long-press FindBack and choose **Pin**. On Samsung, an edit/pencil option may allow adding it to favorites instead. Options vary by Android version and by the sending app.

Android and the sending app control share-target ranking. FindBack cannot force itself into first position, insert itself into Facebook's private shortcut row, or automatically configure each user's favorites. Pinning is a user action where supported.

## Verification

See [Phase 6 verification](PHASE6_VERIFICATION.md) for current native regression, APK, and live-device evidence. The old statement that this repository lacks an Android shell is obsolete.

The Dart service handles missing platform implementations safely. iOS sharing is **not verified or implemented by this Android phase**; an iOS share extension would require separately authorized work.
