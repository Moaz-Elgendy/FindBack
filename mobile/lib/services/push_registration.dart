/// Keeping the backend's device registration in step with this installation,
/// and turning a tap on the weekly note into a screen.
///
/// The weekly note is sent by the server to whatever devices it has on file,
/// so this is the only thing that decides whether the user hears about it. It
/// runs at three moments: when an account signs in, whenever the platform
/// rotates the token, and on the way out when the account signs out.
///
/// Four rules shape it:
///
///   * Nothing here may throw. Registration is not worth failing a sign-in for,
///     and a device with notifications switched off must produce silence, not
///     an error. Every failure path logs and returns.
///   * The token is device state, not account state. Signing out deletes it so
///     the next person to sign in on this phone does not inherit the previous
///     account's notifications; signing in re-registers it.
///   * A tap names a snapshot; it never carries an account. The payload has no
///     account id in it, so the app asks the server whether THIS account may
///     read that snapshot. A snapshot belonging to someone else answers 404 and
///     the screen shows its unavailable state rather than anything else.
///   * A tap while signed out is remembered, not dropped. The user asked to see
///     that list, and signing in is the moment it becomes answerable.
library;

import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/foundation.dart' show debugPrint, visibleForTesting;

import '../data/api_client.dart';
import 'messaging_service.dart';
import 'reminder_notifications.dart';

/// The only notification type this app acts on.
///
/// Anything else is dropped rather than guessed at, so a type the server gains
/// later cannot accidentally navigate somewhere.
const String kWeeklyNoteType = 'weekly_note';

/// Prefix marking a LOCAL notification payload as a weekly note rather than a
/// memory id.
///
/// The reminders channel and the weekly note share one
/// `flutter_local_notifications` instance and therefore one tap handler. A bare
/// payload is a memory id, which is what every existing reminder sends, so
/// without a marker a weekly-note tap would be opened as
/// `DetailPage(itemId: <snapshot id>)` -- a memory screen for something that is
/// not a memory. Reminder payloads are untouched; only this new kind of local
/// notification is prefixed.
const String kWeeklyNotePayloadPrefix = 'weekly_note:';

/// Notification id for a weekly note shown locally.
///
/// One fixed id, so this week's note replaces last week's on the notification
/// shade rather than stacking underneath it. Derived from nothing: a repeat
/// must update in place, and a per-message hash would not.
const int kWeeklyNoteNotificationId = 900001;

/// One device, and the taps that belong to it.
/// The result of `PushRegistration.replayPendingTapFor`: what to open, and why
/// nothing was.
///
/// A small value type rather than a bare `String?` because the caller has to
/// tell "no tap was pending" apart from "a tap was pending and was refused" to
/// decide whether to send the user home.
class PendingReplay {
  const PendingReplay({this.snapshotId, this.ignoredAccount = false});

  /// The snapshot id to open, or null when there was nothing to open.
  final String? snapshotId;

  /// True when a tap WAS held and has now been discarded because it belongs to
  /// a different account than the one signed in.
  final bool ignoredAccount;

  /// True when a note was held and refused.
  bool get refused => ignoredAccount;
}

class PushRegistration {
  PushRegistration({
    required this.api,
    required this.messaging,
    required this.guest,
    this.notifications,
    this.accountId,
    String? platform,
  }) : platform = platform ?? _currentPlatform();

  final ApiClient api;
  final MessagingService messaging;

  /// True for a guest scope. A guest has no account to read a snapshot with,
  /// so taps are held for a sign-in rather than acted on.
  final bool guest;

  /// The account signed in on this scope, or null for a guest.
  ///
  /// Compared against a note's own account id so a notification addressed to
  /// somebody else is refused rather than opened.
  final String? accountId;

  /// Called when a note for a DIFFERENT account is ignored.
  ///
  /// The app uses it to send the user back to the library: the note cannot be
  /// opened, so leaving them on whatever screen they were on when it arrived
  /// looks like the tap did nothing at all.
  void Function()? onForeignTap;

  /// `'android'` or `'ios'`. Injected by tests, since `Platform` is fixed for
  /// the host running them.
  final String platform;

  /// How a foreground note is rendered, using the same plugin the reminders
  /// use. Null in tests that never receive a foreground message.
  final ReminderNotifications? notifications;

  /// The token currently registered with the backend, or null when none is.
  String? _registered;

  StreamSubscription<String>? _refresh;
  StreamSubscription<PushPayload>? _opened;
  StreamSubscription<PushPayload>? _foreground;
  bool _stopped = false;

  /// Weekly-note snapshot ids to open, for a signed-in account.
  final StreamController<String> _taps = StreamController<String>.broadcast();
  Stream<String> get taps => _taps.stream;

  /// A tap that arrived while signed out, to be opened after signing in.
  ///
  /// Static on purpose. `AppServices` -- and therefore this object -- is
  /// discarded and rebuilt on every account switch, so an instance field would
  /// be dropped by exactly the sign-in this exists to serve.
  static PushPayload? _pending;

  /// The snapshot id waiting for a sign-in, or null.
  static String? get pendingTap => _pending?.snapshotId;

  /// The account the pending note belongs to, or null when it did not say.
  static String? get pendingTapAccount => _pending?.accountId;

  /// Takes the pending tap and clears it. Called after signing in.
  ///
  /// Returns the snapshot id only. [pendingAccountId] is checked by the caller
  /// before this is called: a guest scope holds a tap without knowing who it is
  /// for, so the account comparison can only happen once somebody is signed in.
  static String? takePendingTap() {
    final id = _pending?.snapshotId;
    _pending = null;
    return id;
  }

  /// The static outlives a test on purpose; this puts it back.
  @visibleForTesting
  static void clearPendingTap() => _pending = null;

  /// The held tap, if any, already checked against [accountId] and cleared.
  ///
  /// A guest scope holds a tap without knowing which account the note is for,
  /// so this comparison can only happen here, at replay. [ignoredAccount] is
  /// true when the note belonged to a different account and must not be opened
  /// -- the caller sends the user home in that case rather than leaving the tap
  /// looking like it did nothing.
  ///
  /// The tap is cleared either way, so a refused replay cannot be retried
  /// against a later account.
  static PendingReplay replayPendingTapFor(String? accountId) {
    final payload = _pending;
    _pending = null;
    if (payload == null) return const PendingReplay();
    if (accountId != null && !payload.isForAccount(accountId)) {
      debugPrint('[push] held tap belongs to another account; ignoring');
      return const PendingReplay(ignoredAccount: true);
    }
    return PendingReplay(snapshotId: payload.snapshotId);
  }

  /// Hold a weekly-note tap until an account is signed in.
  ///
  /// The same slot a push tap uses while signed out, reached from the LOCAL
  /// notification path, so a weekly note tapped while signed out behaves
  /// identically whether it arrived by push or was drawn by the app.
  ///
  /// The whole payload is kept, not just the id, because the account guard has
  /// to run again at replay time -- a guest scope has no account to compare
  /// against, and by the time the tap is replayed the account is known and the
  /// note may turn out to belong to somebody else.
  static void holdUntilSignIn(PushPayload payload) {
    if (payload.snapshotId.isNotEmpty) _pending = payload;
  }

  /// The token this service last registered. Exposed for tests and diagnostics.
  String? get registeredToken => _registered;

  static String _currentPlatform() {
    if (Platform.isAndroid) return 'android';
    if (Platform.isIOS) return 'ios';
    // Not a phone. The backend rejects anything outside the closed vocabulary,
    // so registration is skipped rather than sent with a value that cannot
    // store.
    return '';
  }

  /// Register this device if the user has allowed notifications, and start
  /// listening for taps.
  ///
  /// Returns true when the backend now knows about this device. False is a
  /// normal outcome, not a failure: permission denied, no Firebase config in
  /// this build, or an unsupported platform.
  Future<bool> start() async {
    if (platform.isEmpty) return false;
    // Cleared, not latched: stop() is called on sign-out, and this instance
    // must still be usable if the same account is opened again.
    _stopped = false;

    // Tap listening happens BEFORE every early return below, and for guests
    // too. A signed-out user who taps the weekly note is the case that most
    // needs handling, and a guest never registers a device, so gating this on
    // registration would make it unreachable.
    await _listenForTaps();
    if (_stopped) return false;

    // A guest has no account to receive on, so there is nothing to register.
    if (guest) return false;

    bool permitted;
    try {
      if (notifications != null && !await notifications!.enabled()) return false;
      permitted = await messaging.requestPermission();
    } on Object catch (failure) {
      // A plugin that throws is treated exactly like a refusal: silence, not an
      // error. This runs on the sign-in path, which must not be failed by a
      // notification feature.
      debugPrint('[push] permission check failed: ${failure.runtimeType}');
      return false;
    }
    if (!permitted) {
      debugPrint('[push] notifications not allowed; skipping registration');
      return false;
    }

    String? token;
    try {
      token = await messaging.token();
    } on Object catch (failure) {
      debugPrint('[push] could not read a registration token: '
          '${failure.runtimeType}');
      return false;
    }
    if (token == null || token.isEmpty) {
      debugPrint('[push] no registration token available');
      return false;
    }
    if (!await _register(token)) return false;
    // The platform rotates the token when the app is reinstalled or restored;
    // the previous one is dead from that moment, so re-register immediately.
    //
    // Guarded, because start() runs again on every app resume. Without this,
    // each resume abandoned the previous subscription and started another, so
    // a single rotation would fire N registrations after N resumes.
    _listenForTokenRefresh();
    return true;
  }

  /// Subscribe to token rotation exactly once.
  ///
  /// Idempotent, and deliberately separate from the registration above: a
  /// resume must still register the current token -- permission may have been
  /// granted in system settings, or the backend may have lost the row -- while
  /// never leaving a second listener behind to fire a duplicate POST.
  void _listenForTokenRefresh() {
    if (_refresh != null) return;
    _refresh = messaging.onTokenRefresh.listen(_onRefresh);
  }

  /// Stop registering and tell the backend to forget this device.
  ///
  /// Safe to call more than once, and safe to call when nothing was ever
  /// registered.
  Future<void> stop() async {
    _stopped = true;
    await _refresh?.cancel();
    _refresh = null;
    await _opened?.cancel();
    _opened = null;
    await _foreground?.cancel();
    _foreground = null;
    // This registration drew `kWeeklyNoteNotificationId` into the shade, so
    // it owns clearing it. Cancelling here rather than relying on the
    // reminders service is what makes the note go away on a scope built
    // without one, and it keeps the ownership where the drawing happened.
    //
    // Guarded: a notification plugin failure must not block the sign-out that
    // is under way, and a note left on the shade is a cosmetic problem next to
    // a user who cannot sign out.
    try {
      await notifications?.cancel(kWeeklyNoteNotificationId);
    } on Object catch (failure) {
      debugPrint('[push] could not clear the weekly note: '
          '${failure.runtimeType}');
    }
    final token = _registered;
    _registered = null;
    if (token == null) return;
    try {
      await api.removeDevice(token);
    } on Object catch (failure) {
      // Failing to unregister must not block the sign-out that is under way.
      // The token is still rotated away by the platform on the next sign-in.
      debugPrint('[push] could not remove device registration: '
          '${failure.runtimeType}');
    }
  }

  /// Wire the three ways a notification can be acted on.
  Future<void> _listenForTaps() async {
    if (_opened != null) return;
    // This initializes Firebase before its stream getters are read, for guests too.
    await _readInitialNotification();
    if (_opened != null || _stopped) return;

    // Background: the app was running, the user tapped the shade.
    _opened = messaging.onNotificationOpened.listen(_onOpened);

    // Foreground: no navigation, but the note is rendered locally. Android
    // suppresses a notification for a message the app is handling, so without
    // this the weekly note is invisible while the app is open. iOS renders it
    // itself, so a local copy there would deliver it twice.
    _foreground = messaging.onForegroundMessage.listen(_onForeground);

  }

  Future<void> _readInitialNotification() async {
    if (_stopped) return;
    PushPayload? payload;
    try {
      payload = await messaging.initialNotification();
    } on Object catch (failure) {
      debugPrint('[push] could not read the launch notification: '
          '${failure.runtimeType}');
      return;
    }
    if (payload == null || _stopped) return;
    _handle(payload);
  }

  void _onOpened(PushPayload payload) {
    if (_stopped) return;
    _handle(payload);
  }

  /// Show a note that arrived while the app was open. It never navigates.
  ///
  /// The text is the server's own notification block, verbatim -- the same
  /// words the OS would have put on the lock screen, so nothing private is
  /// exposed by showing it here. No text is invented when the message carried
  /// none, and no memory title is ever composed locally: the count in the title
  /// is the server's, and the list is fetched after a tap.
  Future<void> _onForeground(PushPayload payload) async {
    // A guest is never registered, so FCM should never reach it -- and a note
    // it cannot open is worse than no note.
    if (_stopped || guest || platform != 'android') return;
    if (payload.type != kWeeklyNoteType) return;
    // Not shown at all when it is addressed to another account, rather than
    // shown and then refused on tap.
    if (!payload.isForAccount(accountId)) return;
    final notifications = this.notifications;
    // A data-only message has nothing to show, and inventing a title here
    // would risk putting content on the lock screen the server never sent.
    if (notifications == null || !payload.hasNotificationText) return;
    try {
      // Awaited, not just called: `showNote` is async, so a plugin failure
      // arrives as a rejected Future and would otherwise escape as an
      // unhandled async error.
      await notifications.showNote(
        id: kWeeklyNoteNotificationId,
        title: payload.title!,
        body: payload.body!,
        // Prefixed so the shared tap handler can tell this from a memory id
        // and open "Worth another look" rather than a memory detail screen.
        payload: '$kWeeklyNotePayloadPrefix${payload.snapshotId}',
      );
    } on Object catch (failure) {
      debugPrint('[push] could not show a foreground note: '
          '${failure.runtimeType}');
    }
  }

  void _handle(PushPayload payload) {
    // Only the weekly note is actionable. A future server-side type must not
    // inherit this behaviour by accident.
    if (payload.type != kWeeklyNoteType) {
      debugPrint('[push] ignoring a "${payload.type}" notification');
      return;
    }
    final id = payload.snapshotId;
    if (id.isEmpty) return;

    // A note addressed to a different account than the one signed in here is
    // not this user's to open. This happens when a device token changed hands:
    // the server sends to whoever owns the token now, and the previous account
    // is the one the note is really about.
    if (!payload.isForAccount(accountId)) {
      debugPrint('[push] ignoring a note addressed to another account');
      onForeignTap?.call();
      return;
    }

    if (guest) {
      // Signed out. The snapshot belongs to an account this scope cannot read,
      // so the tap is held until there is one. It overwrites any earlier
      // pending tap: only the most recent intent is still current.
      _pending = payload;
      debugPrint('[push] holding a tap until sign-in');
      return;
    }

    // One path for all three entry points. The UI binds this stream BEFORE
    // start(), so the terminated-launch read lands here like every other tap
    // rather than needing a separate "initial tap" slot to be drained.
    _taps.add(id);
  }

  Future<void> _onRefresh(String token) async {
    // A refresh after sign-out must not put a signed-out device back on the
    // list; the listener is cancelled by stop(), but a token already in flight
    // can still land here.
    if (_stopped) return;
    // The old token is dead; drop it locally so a later stop() does not try to
    // delete something FCM has already rejected.
    _registered = null;
    await _register(token);
  }

  Future<bool> _register(String token) async {
    if (_stopped) return false;
    try {
      await api.registerDevice(token, platform);
      if (_stopped) {
        await api.removeDevice(token);
        return false;
      }
      _registered = token;
      return true;
    } on Object catch (failure) {
      debugPrint('[push] device registration failed: ${failure.runtimeType}');
      return false;
    }
  }

  Future<void> dispose() async {
    await stop();
    await _taps.close();
  }
}
