import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../utils/share_text.dart';
import 'capture_service.dart';

/// The Share Sheet entry point (Phase 15): Any app -> Share -> FindBack ->
/// Saved.
///
/// The platform hands over the shared text twice over: once on the launch that
/// the share started, and again for any share that arrives while the app is
/// already open. Both paths end here.
///
/// This service only understands the payload. Saving is [CaptureService]'s job,
/// and it saves locally first when the network is down, so a share that arrives
/// over an intent is no more losable than one the user typed.
class ShareIntentService {
  static const String channelName = 'findback/share';

  final MethodChannel _channel;
  bool _disposed = false;

  // A MethodChannel has no stream of its own, so the platform calls a handler
  // and this re-broadcasts. Broadcast, not single-subscription: the Home screen
  // and the save flow may both be listening at once.
  final StreamController<String> _incoming =
      StreamController<String>.broadcast();

  ShareIntentService({MethodChannel? channel})
      : _channel = channel ?? const MethodChannel(ShareIntentService.channelName) {
    _channel.setMethodCallHandler(_onPlatformCall);
  }

  /// Shares arriving while the app is already open.
  ///
  /// The raw shared text. Deciding what the user meant by a share is the
  /// caller's business, so nothing is filtered out here beyond the empty.
  Stream<String> get shares => _incoming.stream;
  final _authIncoming = StreamController<String>.broadcast();
  Stream<String> get authLinks => _authIncoming.stream;
  Future<String?> readInitialAuthLink() async {
    try { return await _channel.invokeMethod<String>('getInitialAuthLink'); }
    on MissingPluginException { return null; }
    on PlatformException { return null; }
  }

  Future<Object?> _onPlatformCall(MethodCall call) async {
    if (call.method == 'onAuthLink') {
      final link = call.arguments;
      if (link is String && link.isNotEmpty) _authIncoming.add(link);
      return null;
    }
    if (call.method != 'onShare') return null;
    final Object? text = call.arguments;
    if (text is String && text.isNotEmpty) _incoming.add(text);
    return null;
  }

  /// Stops listening. Without this the handler outlives the service.
  Future<void> pauseDelivery() async {
    try { await _channel.invokeMethod<void>('pauseDelivery'); }
    on MissingPluginException { return; }
    on PlatformException { return; }
  }

  Future<void> dispose() async {
    if (_disposed) return;
    _disposed = true;
    _channel.setMethodCallHandler(null);
    unawaited(_incoming.close());
    unawaited(_authIncoming.close());
  }

  /// The URL inside a share payload, or null when it is not a link.
  ///
  /// A share is prose with a URL somewhere inside it, so this is the first
  /// point at which the payload is understood at all.
  static String? urlFromShare(String? payload) =>
      extractUrlFromShareText(payload);

  /// The share that started this launch, if the app was launched by one.
  ///
  /// Never throws. A platform with no implementation is not an error here:
  /// sharing is an entry point, and the app must still launch normally without
  /// it.
  Future<String?> readInitialShare() async {
    try {
      final String? text = await _channel.invokeMethod<String>('getInitialShare');
      return (text == null || text.isEmpty) ? null : text;
    } on MissingPluginException {
      return null;
    } on PlatformException catch (error) {
      debugPrint('[share] could not read the initial share: ${error.code}');
      return null;
    }
  }

  /// Saves a share payload, offline or not, and reports what happened.
  ///
  /// Returns null when the payload held no URL: there was nothing to save, and
  /// the user should not be told a save happened.
  ///
  /// Each unique URL uses the capture path. Failed URLs remain in the result.
  Future<CaptureBatch?> captureShared(
    String payload,
    CaptureService capture,
  ) async {
    if (extractUrlsFromShareText(payload).isEmpty) return null;
    return capture.captureText(payload);
  }

  /// Saves the share that launched the app, if there was one.
  Future<CaptureBatch?> captureInitialShare(CaptureService capture) async {
    final String? payload = await readInitialShare();
    if (payload == null) return null;
    return captureShared(payload, capture);
  }
}
