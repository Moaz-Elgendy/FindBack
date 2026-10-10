"""Compile the real MainActivity against tiny Android/Flutter boundary doubles.
Run after a Gradle build has populated the Kotlin compiler cache. No downloads.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

mobile = Path(__file__).resolve().parents[1]
cache = Path.home() / '.gradle/caches/modules-2/files-2.1'
compiler = next(cache.glob('org.jetbrains.kotlin/kotlin-compiler-embeddable/2.2.21/*/*.jar'))
jars = [compiler]
for artifact in ['kotlin-stdlib', 'kotlin-script-runtime', 'kotlin-daemon-embeddable']:
    jars.extend(cache.glob(f'org.jetbrains.kotlin/{artifact}/2.2.21/*/*.jar'))
jars.extend(cache.glob('org.jetbrains.kotlin/kotlin-reflect/1.6.10/*/*.jar'))
jars.extend(cache.glob('org.jetbrains.kotlinx/kotlinx-coroutines-core-jvm/1.8.0/*/*.jar'))
jars.extend(cache.glob('org.jetbrains/annotations/23.0.0/*/*.jar'))
classpath = os.pathsep.join(map(str, jars))
stdlib = next(cache.glob('org.jetbrains.kotlin/kotlin-stdlib/2.2.21/*/*.jar'))

sources = {
    'Settings.kt': '''package android.provider
object Settings {
    const val ACTION_APP_NOTIFICATION_SETTINGS = "settings"
    const val EXTRA_APP_PACKAGE = "package"
}''',
    'Intent.kt': '''package android.content
class Intent(val action: String? = null, val text: CharSequence? = null,
    val texts: ArrayList<CharSequence>? = null, val data: android.net.Uri? = null) {
    fun getCharSequenceExtra(key: String): CharSequence? = text
    fun getCharSequenceArrayListExtra(key: String): ArrayList<CharSequence>? = texts
    fun putExtra(key: String, value: String): Intent = this
    companion object {
        const val ACTION_VIEW = "android.intent.action.VIEW"
        const val ACTION_SEND = "android.intent.action.SEND"
        const val ACTION_SEND_MULTIPLE = "android.intent.action.SEND_MULTIPLE"
        const val EXTRA_TEXT = "android.intent.extra.TEXT"
    }
}''',
    'Uri.kt': '''package android.net
class Uri(private val value: String) {
    private val parsed = java.net.URI(value)
    val scheme = parsed.scheme
    val host = parsed.host
    val path = parsed.path
    override fun toString() = value
}''',
    'FlutterEngine.kt': '''package io.flutter.embedding.engine
class FlutterEngine {
    val dartExecutor = Executor()
    class Executor { val binaryMessenger = Any() }
}''',
    'FlutterActivity.kt': '''package io.flutter.embedding.android
import android.content.Intent
import io.flutter.embedding.engine.FlutterEngine
open class FlutterActivity {
    @set:JvmName("storeIntent")
    var intent = Intent()
    val packageName = "com.findback.findback"
    fun startActivity(value: Intent) {}
    fun setIntent(value: Intent) { intent = value }
    open fun configureFlutterEngine(engine: FlutterEngine) {}
    open fun onNewIntent(intent: Intent) {}
}''',
    'MethodChannel.kt': '''package io.flutter.plugin.common
class MethodCall(val method: String)
class MethodChannel(messenger: Any, name: String) {
    interface Result { fun success(value: Any?); fun notImplemented() }
    fun setMethodCallHandler(handler: (MethodCall, Result) -> Unit) { receiver = handler }
    fun invokeMethod(method: String, value: Any?) { emitted.add(value) }
    companion object {
        lateinit var receiver: (MethodCall, Result) -> Unit
        val emitted = mutableListOf<Any?>()
        fun initial(method: String = "getInitialShare"): Any? {
            var answer: Any? = null
            receiver(MethodCall(method), object: Result {
                override fun success(value: Any?) { answer = value }
                override fun notImplemented() { error("Missing initial share handler") }
            })
            return answer
        }
    }
}''',
    'Check.kt': '''import android.content.Intent
import com.findback.findback.MainActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
fun main() {
    val activity = MainActivity()
    activity.setIntent(Intent(Intent.ACTION_SEND, "https://example.test/cold"))
    activity.configureFlutterEngine(FlutterEngine())
    activity.onNewIntent(Intent(Intent.ACTION_SEND, "https://example.test/early"))
    check(MethodChannel.emitted.isEmpty()) { "Share emitted before Dart listener was ready" }
    check(MethodChannel.initial() == "https://example.test/cold\\nhttps://example.test/early")
    check(MethodChannel.initial() == null) { "Initial share replayed" }
    activity.onNewIntent(Intent(Intent.ACTION_SEND, "https://example.test/warm"))
    check(MethodChannel.emitted.single() == "https://example.test/warm")
    val links = ArrayList<CharSequence>((1..20).map { "https://example.test/$it" })
    activity.onNewIntent(Intent(Intent.ACTION_SEND_MULTIPLE, texts = links))
    check(MethodChannel.emitted.last() == links.joinToString("\\n"))
    activity.onNewIntent(Intent("unrelated", "https://example.test/ignored"))
    check(MethodChannel.emitted.size == 2)
    val recovery = "findback://auth/recovery#access_token=x&refresh_token=y&type=recovery"
    activity.onNewIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(recovery)))
    check(MethodChannel.emitted.size == 2)
    check(MethodChannel.initial("getInitialAuthLink") == recovery)
    check(MethodChannel.initial("getInitialAuthLink") == null)
    activity.onNewIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(recovery)))
    check(MethodChannel.emitted.last() == recovery)
    val sharedMemory = "https://findback.duckdns.org/s/" + "a".repeat(43)
    activity.onNewIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(sharedMemory)))
    check(MethodChannel.emitted.last() == sharedMemory)
    val sharedCold = MainActivity()
    sharedCold.setIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(sharedMemory)))
    sharedCold.configureFlutterEngine(FlutterEngine())
    check(MethodChannel.initial("getInitialAuthLink") == sharedMemory)
    check(MethodChannel.initial("getInitialAuthLink") == null)
    activity.configureFlutterEngine(FlutterEngine())
    MethodChannel.initial("getInitialAuthLink")
    MethodChannel.initial("pauseDelivery")
    val emittedBeforePause = MethodChannel.emitted.size
    activity.onNewIntent(Intent(Intent.ACTION_SEND, "https://example.test/paused"))
    activity.onNewIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(recovery)))
    check(MethodChannel.emitted.size == emittedBeforePause)
    check(MethodChannel.initial("getInitialAuthLink") == recovery)
    check(MethodChannel.initial() == "https://example.test/paused")
    val cold = MainActivity()
    cold.setIntent(Intent(Intent.ACTION_VIEW, data = android.net.Uri(recovery)))
    cold.configureFlutterEngine(FlutterEngine())
    cold.onNewIntent(Intent(Intent.ACTION_SEND, "https://example.test/preserved"))
    check(MethodChannel.initial("getInitialAuthLink") == recovery)
    check(MethodChannel.initial() == "https://example.test/preserved")
    println("PASS: pause/resume buffering, auth cold/warm buffering, independent share queue, startup buffering, consume-once, warm share, twenty links, unrelated intent")
}''',
}
with tempfile.TemporaryDirectory(prefix='findback-native-share-') as tmp:
    root = Path(tmp)
    for name, content in sources.items():
        (root / name).write_text(content)
    output = root / 'classes'
    subprocess.run(['java', '-cp', classpath, 'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler',
                    '-no-stdlib', '-no-reflect', '-classpath', str(stdlib), '-d', str(output),
                    *map(str, root.glob('*.kt')),
                    str(mobile / 'android/app/src/main/kotlin/com/findback/findback/MainActivity.kt')], check=True)
    subprocess.run(['java', '-cp', os.pathsep.join([str(output), str(stdlib)]), 'CheckKt'], check=True)

manifest = ET.parse(mobile / 'android/app/src/main/AndroidManifest.xml')
android = '{http://schemas.android.com/apk/res/android}'
assert manifest.find('application').get(android + 'label') == 'FindBack'
filters = manifest.findall('application/activity/intent-filter')
for action in ['android.intent.action.SEND', 'android.intent.action.SEND_MULTIPLE']:
    assert any(any(a.get(android + 'name') == action for a in f.findall('action'))
               and any(d.get(android + 'mimeType') == 'text/plain' for d in f.findall('data')) for f in filters)
print('PASS: FindBack label and single/multiple text intent registration')

assert any(any(a.get(android + 'name') == 'android.intent.action.VIEW' for a in f.findall('action'))
           and any(d.get(android + 'scheme') == 'findback' and d.get(android + 'host') == 'auth'
                   and d.get(android + 'path') == '/recovery' for d in f.findall('data')) for f in filters)
print('PASS: native recovery link registration')
