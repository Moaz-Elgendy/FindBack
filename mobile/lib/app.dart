import 'dart:async';
import 'package:flutter/material.dart';

import 'app_services.dart';
import 'features/collections/library_shell.dart';
import 'features/account/account_page.dart';
import 'features/home/detail_page.dart';
import 'services/account_coordinator.dart';
import 'services/appearance.dart';
import 'theme.dart';

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
  AppServices? _boundReminders;
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
  }

  void _bindReminders() {
    if (_boundReminders == services) return;
    _boundReminders = services;
    _reminderTaps?.cancel();
    final reminder = services.reminders;
    if (reminder == null) return;
    final bound = services;
    _reminderTaps = reminder.taps.listen((id) => _openReminder(id, bound));
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        await reminder.start();
        final id = reminder.initialTap;
        reminder.initialTap = null;
        if (id != null) _openReminder(id, bound);
      } catch (_) { /* A device plugin failure cannot prevent opening the library. */ }
    });
  }

  void _openReminder(String id, AppServices bound) {
    if (!mounted || services != bound) return;
    _navigator.currentState?.push(MaterialPageRoute<void>(builder: (_) =>
      DetailPage(itemId: id, items: bound.items, services: bound)));
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      services.reminders?.reconcile().catchError((Object _) {});
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
      if (link != null) await _recovery(link);
    });
  }

  Future<void> _recovery(String link) async {
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

  void _changed() {
    if (!mounted) return;
    _navigator.currentState?.popUntil((route) => route.isFirst);
    setState(() {});
    _bindLinks();
    _bindReminders();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _reminderTaps?.cancel();
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
