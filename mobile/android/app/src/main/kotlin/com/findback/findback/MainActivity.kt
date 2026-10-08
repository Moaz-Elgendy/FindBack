package com.findback.findback

import android.content.Intent
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null
    private var dartReady = false
    private var authReady = false
    private var pendingAuthLink: String? = null
    private val pendingShares = mutableListOf<String>()

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "findback/notifications").setMethodCallHandler { call, result ->
            if (call.method == "openSettings") {
                startActivity(Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                    .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName))
                result.success(null)
            } else result.notImplemented()
        }
        pendingAuthLink = authLink(intent)
        val initialShare = sharedText(intent)
        initialShare?.let { pendingShares.add(it) }
        // Keep notification launch extras for the notification plugin.
        if (pendingAuthLink != null || initialShare != null) setIntent(Intent())
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "findback/share")
        channel!!.setMethodCallHandler { call, result ->
            if (call.method == "getInitialShare") {
                val initial = pendingShares.takeIf { it.isNotEmpty() }?.joinToString("\n")
                pendingShares.clear()
                dartReady = true
                result.success(initial)
            } else if (call.method == "pauseDelivery") {
                dartReady = false
                authReady = false
                result.success(null)
            } else if (call.method == "getInitialAuthLink") {
                authReady = true
                result.success(pendingAuthLink)
                pendingAuthLink = null
            } else {
                result.notImplemented()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        authLink(intent)?.let { link ->
            if (authReady) channel?.invokeMethod("onAuthLink", link) else pendingAuthLink = link
            setIntent(Intent())
            return
        }
        val text = sharedText(intent) ?: return
        if (dartReady) {
            channel?.invokeMethod("onShare", text)
        } else {
            pendingShares.add(text)
        }
        setIntent(Intent())
    }

    private fun authLink(intent: Intent): String? {
        if (intent.action != Intent.ACTION_VIEW) return null
        val uri = intent.data ?: return null
        return if (uri.scheme == "findback" && uri.host == "auth" && uri.path == "/recovery") uri.toString() else null
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
