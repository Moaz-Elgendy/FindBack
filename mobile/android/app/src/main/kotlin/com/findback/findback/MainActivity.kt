package com.findback.findback

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "findback/share")
        channel!!.setMethodCallHandler { call, result ->
            if (call.method == "getInitialShare") {
                result.success(sharedText(intent))
                intent = Intent()
            } else {
                result.notImplemented()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val text = sharedText(intent) ?: return
        channel?.invokeMethod("onShare", text)
        setIntent(Intent())
    }

    private fun sharedText(intent: Intent): String? {
        return when (intent.action) {
            Intent.ACTION_SEND -> intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()
            Intent.ACTION_SEND_MULTIPLE -> intent.getCharSequenceArrayListExtra(Intent.EXTRA_TEXT)
                ?.joinToString("\n")
            else -> null
        }
    }
}
