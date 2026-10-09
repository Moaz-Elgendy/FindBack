/// The push transport, behind an interface.
///
/// This exists so the registration logic can be tested without Firebase, a
/// device, or the platform config files -- the same reason
/// `ReminderNotifications` is an interface in this codebase rather than a
/// direct plugin call.
///
/// Firebase is OPTIONAL at runtime. `Firebase.initializeApp()` reads
/// `google-services.json` / `GoogleService-Info.plist` from the native side and
/// throws when they are absent, so a build without those files would crash at
/// startup if this were called unguarded. Every entry point here returns a
/// neutral answer instead, and push is simply unavailable.
library;

import 'dart:async';

import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

/// The parts of a notification this app acts on.
///
/// The server sends `{"type":"weekly_note","snapshot_id":"..."}` and nothing
/// else, so this is the whole payload. It carries no account id and no memory
/// content: a tap identifies a snapshot, and the app then asks the server
/// whether this account may read it.
class PushPayload {
  const PushPayload({required this.type, required this.snapshotId,
    this.accountId, this.title, this.body});

  /// Builds from a raw `data` map, or null when it is not a usable message.
  ///
  /// [title] and [body] come from the FCM `notification` block, NOT from the
  /// data map: the data payload stays exactly `{type, snapshot_id}` because it
  /// is the part that gets logged, cached and shown in developer tools. The
  /// notification block is the same text the OS would have rendered anyway, so
  /// forwarding it exposes nothing new.
  static PushPayload? fromData(Map<String, dynamic>? data,
      {String? title, String? body}) {
    if (data == null) return null;
    final type = data['type'];
    final snapshotId = data['snapshot_id'];
    if (type is! String || type.isEmpty) return null;
    if (snapshotId is! String || snapshotId.isEmpty) return null;
    final accountId = data['account_id'];
    return PushPayload(
        type: type,
        snapshotId: snapshotId,
        accountId: accountId is String && accountId.isNotEmpty ? accountId : null,
        title: title,
        body: body);
  }

  final String type;
  final String snapshotId;

  /// The account this note belongs to, or null when the server did not say.
  ///
  /// Used to refuse a note addressed to a different account than the one
  /// signed in. Null is treated as "cannot tell", which keeps older payloads
  /// working rather than locking the user out of their own note.
  final String? accountId;

  /// True when this note is addressed to [currentAccountId] and may be opened.
  ///
  /// A null [accountId] on either side passes: an unknown sender or a guest
  /// scope has nothing to compare, and the snapshot endpoint still refuses
  /// anything the caller may not read.
  bool isForAccount(String? currentAccountId) {
    if (accountId == null || currentAccountId == null) return true;
    return accountId == currentAccountId;
  }

  /// The lock-screen text, when the message carried a notification block.
  final String? title;
  final String? body;

  /// True when there is server-composed text to show locally.
  bool get hasNotificationText =>
      title != null && title!.isNotEmpty && body != null && body!.isNotEmpty;

  @override
  bool operator ==(Object other) =>
      other is PushPayload &&
      other.type == type &&
      other.snapshotId == snapshotId &&
      other.title == title &&
      other.body == body;

  @override
  int get hashCode => Object.hash(type, snapshotId, title, body);

  @override
  String toString() => 'PushPayload($type, $snapshotId)';
}

abstract class MessagingService {
  /// Whether the Firebase SDK could start. False means the platform config
  /// files are missing, which is a supported state for a local build.
  bool get isAvailable;

  /// Ask the user for notification permission.
  ///
  /// Returns false when permission is denied, and must not throw: a refusal is
  /// a normal outcome the caller skips over, not an error to surface.
  Future<bool> requestPermission();

  /// The current registration token, or null when there is none.
  Future<String?> token();

  /// Fires when the token is rotated by the platform; the old one is dead.
  Stream<String> get onTokenRefresh;

  /// A message delivered while the app is in the FOREGROUND.
  ///
  /// Deliberately not used to navigate or to display anything. The notification
  /// is already on screen -- the server sent an `FCM notification` block, which
  /// the system renders -- so acting on this would put the user somewhere they
  /// did not ask to be. It is subscribed to only so the app and the SDK do not
  /// both handle the same message.
  Stream<PushPayload> get onForegroundMessage;

  /// A message the user tapped while the app was in the BACKGROUND.
  Stream<PushPayload> get onNotificationOpened;

  /// The message that launched the app from a TERMINATED state, or null.
  Future<PushPayload?> initialNotification();

  Future<void> dispose();
}

class FirebaseMessagingService implements MessagingService {
  FirebaseMessaging? _messaging;

  /// A failed initialize is remembered rather than retried per call: without
  /// the config files it would fail identically every time, and the weekly-note
  /// tick path calls this on each launch.
  bool _unavailable = false;

  Future<FirebaseMessaging?> _ready() async {
    if (_messaging != null) return _messaging;
    if (_unavailable) return null;
    try {
      await Firebase.initializeApp();
      return _messaging = FirebaseMessaging.instance;
    } on Object catch (_) {
      // No google-services.json / GoogleService-Info.plist in this build.
      _unavailable = true;
      return null;
    }
  }

  @override
  bool get isAvailable => !_unavailable;

  @override
  Future<bool> requestPermission() async {
    final messaging = await _ready();
    if (messaging == null) return false;
    try {
      final settings = await messaging.requestPermission(
        alert: true, badge: true, sound: true);
      // Android 13+ reports through `provisional`; anything not explicitly
      // denied counts as permitted.
      return settings.authorizationStatus != AuthorizationStatus.denied;
    } on Object catch (_) {
      return false;
    }
  }

  @override
  Future<String?> token() async {
    final messaging = await _ready();
    if (messaging == null) return null;
    try {
      return await messaging.getToken();
    } on Object catch (_) {
      return null;
    }
  }

  @override
  Stream<String> get onTokenRefresh {
    final messaging = _messaging;
    if (messaging == null) return const Stream<String>.empty();
    return messaging.onTokenRefresh;
  }

  @override
  Stream<PushPayload> get onForegroundMessage {
    if (_messaging == null) return const Stream<PushPayload>.empty();
    return FirebaseMessaging.onMessage
        .map((message) => PushPayload.fromData(message.data,
            title: message.notification?.title, body: message.notification?.body))
        .where((payload) => payload != null)
        .cast<PushPayload>();
  }

  @override
  Stream<PushPayload> get onNotificationOpened {
    if (_messaging == null) return const Stream<PushPayload>.empty();
    return FirebaseMessaging.onMessageOpenedApp
        .map((message) => PushPayload.fromData(message.data,
            title: message.notification?.title, body: message.notification?.body))
        .where((payload) => payload != null)
        .cast<PushPayload>();
  }

  @override
  Future<PushPayload?> initialNotification() async {
    final messaging = await _ready();
    if (messaging == null) return null;
    try {
      final message = await messaging.getInitialMessage();
      return message == null
          ? null
          : PushPayload.fromData(message.data,
              title: message.notification?.title,
              body: message.notification?.body);
    } on Object catch (_) {
      return null;
    }
  }

  @override
  Future<void> dispose() async {
    _messaging = null;
  }
}