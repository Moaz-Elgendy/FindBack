# Native Share (Phase 15)

The Dart half of `Any app -> Share -> FindBack -> Saved` is implemented and
tested (`lib/services/share_intent_service.dart`, `test/share_intent_test.dart`).
**The native half is not, because this repository contains no `android/` or
`ios/` project** — there is no `AndroidManifest.xml`, no `MainActivity`, and no
Xcode project to attach a share extension to. Only `lib/`, `test/` and the
pubspec are tracked.

What follows is exactly what has to be added once a platform shell exists. The
Dart side already speaks this protocol, so nothing here changes `lib/`.

## The contract

One method channel: `findback/share`.

| Direction | Method | Payload |
|---|---|---|
| Dart → platform | `getInitialShare` | — returns `String?` |
| platform → Dart | `onShare` | the shared `String` |

A payload is raw shared text, not a URL. `ShareIntentService.urlFromShare` pulls
the URL out of it, so the platform does not have to parse anything.

## Android

**`android/app/src/main/AndroidManifest.xml`** — declare the app as a share
target on the existing launcher activity:

```xml
<intent-filter>
    <action android:name="android.intent.action.SEND" />
    <category android:name="android.intent.category.DEFAULT" />
    <data android:mimeType="text/plain" />
</intent-filter>
```

**`MainActivity.kt`** — register the channel, answer the launch intent, and
forward later ones. The launch intent is held, because the channel is only
ready once the engine is up.

```kotlin
class MainActivity : FlutterActivity() {
    private var pending: String? = null
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(engine: FlutterEngine) {
        super.configureFlutterEngine(engine)
        channel = MethodChannel(engine.dartExecutor.binaryMessenger, "findback/share")
        channel!!.setMethodCallHandler { call, result ->
            if (call.method == "getInitialShare") { result.success(pending); pending = null }
            else result.notImplemented()
        }
        handle(intent)
    }

    // onNewIntent covers a share that arrives while the app is already open.
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); handle(intent) }

    private fun handle(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND) return
        val text = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        val ch = channel
        if (ch == null) pending = text else ch.invokeMethod("onShare", text)
    }
}
```

Also add `<queries>` for `android.intent.action.SEND` if the app is ever to
enumerate shares — it is not, so this is not required today.

## iOS

iOS has no equivalent intent filter. Sharing to an app needs a **Share
Extension** target in the Xcode project, which cannot be added by editing
files: it is a target, an `Info.plist`, an entitlements entry and a build
phase.

The extension's job is small and is the only native part:

1. Read the shared text from the extension's `NSExtensionItem` attachments.
2. Write it into the app group (`UserDefaults(suiteName: "group.findback")`).
3. `openURL` the app.

`ShareIntentService` must then read that group before answering
`getInitialShare`. That read is the one piece of Dart that the unit tests do
**not** cover today, because there is no iOS project to build against; it is
the honest gap in this phase.

## Why sharing cannot lose a capture

A share is not a second save path. It calls the same `CaptureService.capture`,
which queues locally when the network is down. So a share taken on a train
lands in `sync_queue` exactly like a typed save, is visible in offline search,
shows in the pending badge, and goes to the server on reconnect — with no new
failure mode to get wrong.
