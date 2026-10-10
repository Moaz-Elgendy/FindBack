import 'config.dart';
import 'data/api_client.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'services/share_links.dart';
import 'widgets/feedback.dart';
import 'dart:async';
import 'package:flutter/material.dart';

import 'app_services.dart';
import 'features/collections/library_shell.dart';
import 'features/account/account_page.dart';
import 'features/home/detail_page.dart';
import 'features/weekly_note/worth_another_look_page.dart';
import 'services/account_coordinator.dart';
import 'services/messaging_service.dart';
import 'services/push_registration.dart';
import 'services/appearance.dart';
import 'theme.dart';

/// Opens whatever a LOCAL notification tap refers to. Returns whether it did.
///
/// Reminders and the foreground weekly note share one
/// `flutter_local_notifications` instance and therefore one tap handler, so the
/// payload is dispatched here. A bare payload is a memory id and opens the
/// detail screen exactly as it always has; only a `weekly_note:` payload takes
/// the other branch. **Reminder payloads are unchanged**, so no existing
/// notification behaves differently.
///
/// A weekly note tapped while signed out is held in the same slot a push tap
/// uses and opened after sign-in, because there is no account to read the
/// snapshot with and opening it immediately would only ever show the
/// unavailable state.
///
/// Returns false when the payload addresses nothing -- an empty or blank id --
/// in which case nothing is opened, rather than a permanently unavailable list.
bool openNotificationTap(String payload, GlobalKey<NavigatorState> navigator,
    AppServices bound) {
  if (payload.startsWith(kWeeklyNotePayloadPrefix)) {
    final snapshotId = payload.substring(kWeeklyNotePayloadPrefix.length);
    if (snapshotId.trim().isEmpty) return false;
    final push = bound.push;
    if (push == null) return false;
    if (push.guest) {
      PushRegistration.holdUntilSignIn(PushPayload(
          type: 'weekly_note', snapshotId: snapshotId));
      return true;
    }
    navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) =>
        WorthAnotherLookPage(snapshotId: snapshotId, services: bound)));
    return true;
  }
  navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) =>
      DetailPage(itemId: payload, items: bound.items, services: bound)));
  return true;
}

/// Root widget. Theme only — dependencies arrive through [AppServices].
class FindBackApp extends StatefulWidget {
  const FindBackApp({super.key, required this.services, this.accounts, this.appearance});

  final AppServices services;
  final AccountCoordinator? accounts;
  final Appearance? appearance;

  @override
  State<FindBackApp> createState() => _FindBackAppState();
}

class _FindBackAppState extends State<FindBackApp> with WidgetsBindingObserver {
  final _navigator = GlobalKey<NavigatorState>();
  StreamSubscription<String>? _authLinks;
  StreamSubscription<String>? _reminderTaps;
  StreamSubscription<String>? _pushTaps;
  AppServices? _boundReminders;
  AppServices? _boundPush;
  late final Appearance _appearance;
  AppServices get services => widget.accounts?.services ?? widget.services;

  @override
  void initState() {
    super.initState();
    _appearance = widget.appearance ?? Appearance();
    widget.accounts?.addListener(_changed);
    _bindLinks();
    WidgetsBinding.instance.addObserver(this);
    _bindReminders();
    _bindWeeklyNote();
  }

  /// Weekly-note taps from the push notification.
  ///
  /// A tap carries only a snapshot id -- no account, and no memory content --
  /// so the screen decides what it may show by asking the server. Someone
  /// else's snapshot answers 404 and the screen says so; nothing is inferred
  /// from the id here.
  void _bindWeeklyNote() {
    if (_boundPush == services) return;
    _boundPush = services;
    _pushTaps?.cancel();
    final push = services.push;
    if (push == null) return;
    final bound = services;
    // A note for another account cannot be opened, so send the user home
    // rather than leaving the tap looking like a no-op.
    push.onForeignTap = _goHome;
    _pushTaps = push.taps.listen((id) => _openWeeklyNote(id, bound));
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        // A tap that launched the app from a terminated state arrives on the
        // stream above, because the listener is bound before start().
        await push.start();
        if (!mounted) return;
        // A tap that arrived while signed out, replayed now that there is an
        // account to read the snapshot with.
        //
        // The account guard runs HERE, not when the tap was held: a guest scope
        // had no account to compare the note against, and the note may turn out
        // to belong to a different account than the one just signed in.
        final replay = PushRegistration.replayPendingTapFor(push.accountId);
        // A refused replay goes home, so the tap is not a silent no-op. An empty
        // one means no tap was pending at all, which is the ordinary case.
        if (replay.refused) {
          _goHome();
          return;
        }
        final pending = replay.snapshotId;
        if (pending != null) _openWeeklyNote(pending, bound);
      } catch (_) {
        /* A device plugin failure cannot prevent opening the library. */
      }
    });
  }

  /// Back to the library, wherever the user currently is.
  void _goHome() {
    if (!mounted) return;
    _navigator.currentState?.popUntil((route) => route.isFirst);
  }

  void _openWeeklyNote(String snapshotId, AppServices bound) {
    if (!mounted || services != bound) return;
    _navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) =>
      WorthAnotherLookPage(snapshotId: snapshotId, services: bound)));
  }

  void _bindReminders() {
    if (_boundReminders == services) return;
    _boundReminders = services;
    _reminderTaps?.cancel();
    final reminder = services.reminders;
    if (reminder == null) return;
    final bound = services;
    _reminderTaps =
        reminder.taps.listen((payload) => _openNotification(payload, bound));
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        await reminder.start();
        final payload = reminder.initialTap;
        reminder.initialTap = null;
        if (payload != null) _openNotification(payload, bound);
      } catch (_) { /* A device plugin failure cannot prevent opening the library. */ }
    });
  }

  /// Opens whatever a LOCAL notification tap refers to.
  ///
  /// The body lives in [openNotificationTap] so it can be exercised directly;
  /// this only adds the "is this still the live account?" guard.
  void _openNotification(String payload, AppServices bound) {
    if (!mounted || services != bound) return;
    openNotificationTap(payload, _navigator, bound);
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      services.reminders?.reconcile().catchError((Object _) {});
      _resumePush();
    }
  }

  /// Re-check notification permission every time the app comes back.
  ///
  /// Permission is granted in system settings, which takes the app out of the
  /// foreground and back, so the app cannot tell the moment it changes. The
  /// token is only registered when permission was allowed, so without this a
  /// user who turns notifications on in Settings would never receive a note
  /// until they happened to sign in again.
  Future<void> _resumePush() async {
    try {
      await services.push?.start();
    } catch (_) {
      // A device plugin failure must never stop the library from opening.
    }
  }

  void _bindLinks() {
    _authLinks?.cancel();
    if (widget.accounts == null) return;
    _authLinks = services.share.authLinks.listen(_recovery);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await widget.accounts!.ready;
      if (!mounted) return;
      final link = await services.share.readInitialAuthLink();
      if (link != null) {
        await _recovery(link);
      } else {
        await _resumeShare();
      }
    });
  }

  Future<void> _recovery(String link) async {
    final token = shareToken(link);
    if (token != null) {
      await _shareStorage.write(key: _pendingShareKey, value: token);
      await _resumeShare(promptSignIn: true);
      return;
    }
    final accounts = widget.accounts;
    if (accounts == null) return;
    await accounts.ready;
    try {
      if (!await accounts.auth.handleRecoveryLink(link)) return;
      await accounts.settled;
      if (mounted) {
        _navigator.currentState?.push(MaterialPageRoute<void>(
            builder: (_) => AccountPage(auth: accounts.auth, recovery: true)));
      }
    } catch (_) {
      if (mounted) {
        final context = _navigator.currentContext;
        if (context != null && context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              const SnackBar(content: Text('This password reset link could not be verified. Request a new one.')));
        }
      }
    }
  }

  static final _pendingShareKey = 'findback.pendingShareToken.${Uri.parse(AppConfig.apiBaseUrl).origin}';
  final _shareStorage = const FlutterSecureStorage();
  bool _redeemingShare = false;

  Future<void> _resumeShare({bool promptSignIn = false}) async {
    final accounts = widget.accounts;
    if (accounts == null || _redeemingShare) return;
    await accounts.ready;
    await accounts.settled;
    final token = await _shareStorage.read(key: _pendingShareKey);
    if (token == null || !mounted || _redeemingShare) return;
    if (accounts.auth.currentSession == null) {
      if (promptSignIn) {
        final context = _navigator.currentContext;
        if (context != null && context.mounted) showFindBackToast(context, 'Sign in to save this shared memory.');
        await _navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) => AccountPage(auth: accounts.auth)));
        if (mounted) await _resumeShare();
      }
      return;
    }
    _redeemingShare = true;
    final bound = services;
    final accountId = accounts.auth.currentSession!.id;
    try {
      final item = await bound.api.redeemShare(token);
      if (!mounted || accounts.auth.currentSession?.id != accountId) return;
      await bound.db.upsertRemoteItems([item]);
      if (!mounted || accounts.auth.currentSession?.id != accountId) return;
      if (await _shareStorage.read(key: _pendingShareKey) == token) {
        await _shareStorage.delete(key: _pendingShareKey);
      }
      _navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) =>
        DetailPage(itemId: item.id, items: bound.items, services: bound)));
    } catch (error) {
      if (!mounted || accounts.auth.currentSession?.id != accountId) return;
      final context = _navigator.currentContext;
      if (error is ApiException && (error.statusCode == 404 || error.statusCode == 410)) {
        if (await _shareStorage.read(key: _pendingShareKey) == token) await _shareStorage.delete(key: _pendingShareKey);
      }
      if (context != null && context.mounted) {
        showFindBackToast(context, error is ApiException && error.statusCode == 410
          ? 'This share link has expired or been revoked.' : 'Could not save this shared memory. Open the link to try again.');
      }
    } finally {
      _redeemingShare = false;
      final next = await _shareStorage.read(key: _pendingShareKey);
      if (mounted && next != null && (next != token || accounts.auth.currentSession?.id != accountId || !identical(services, bound))) unawaited(_resumeShare());
    }
  }

  void _changed() {
    if (!mounted) return;
    _navigator.currentState?.popUntil((route) => route.isFirst);
    setState(() {});
    _bindLinks();
    _bindReminders();
    _bindWeeklyNote();
    WidgetsBinding.instance.addPostFrameCallback((_) { if (mounted) unawaited(_resumeShare()); });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _reminderTaps?.cancel();
    _pushTaps?.cancel();
    widget.accounts?.removeListener(_changed);
    _authLinks?.cancel();
    if (widget.appearance == null) _appearance.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AppearanceScope(appearance: _appearance,
      child: ListenableBuilder(listenable: _appearance, builder: (context, child) => MaterialApp(
      title: 'FindBack',
      navigatorKey: _navigator,
          builder: (context, child) => AccountCoordinatorScope(accounts: widget.accounts,
            child: AppServicesScope(services: services, child: child!)),
      debugShowCheckedModeBanner: false,
      theme: FindBackTheme.build(Brightness.light),
      darkTheme: FindBackTheme.build(Brightness.dark),
      themeMode: _appearance.mode,
      home: LibraryShell(key: ObjectKey(services), services: services, auth: widget.accounts?.auth),
    )));
  }
}
