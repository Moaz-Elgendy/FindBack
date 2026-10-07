import 'dart:async';
import 'package:flutter/material.dart';

import 'app_services.dart';
import 'features/home/home_screen.dart';
import 'features/account/account_page.dart';
import 'services/account_coordinator.dart';

/// Root widget. Theme only — dependencies arrive through [AppServices].
class FindBackApp extends StatefulWidget {
  const FindBackApp({super.key, required this.services, this.accounts});

  final AppServices services;
  final AccountCoordinator? accounts;

  @override
  State<FindBackApp> createState() => _FindBackAppState();
}

class _FindBackAppState extends State<FindBackApp> {
  final _navigator = GlobalKey<NavigatorState>();
  StreamSubscription<String>? _authLinks;
  AppServices get services => widget.accounts?.services ?? widget.services;

  @override
  void initState() {
    super.initState();
    widget.accounts?.addListener(_changed);
    _bindLinks();
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
  }

  @override
  void dispose() {
    widget.accounts?.removeListener(_changed);
    _authLinks?.cancel();
    super.dispose();
  }

  /// The RN client's accent (`#1769aa`) kept as the seed so the brand survives
  /// the rewrite.
  static const Color _seed = Color(0xFF1769AA);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FindBack',
      navigatorKey: _navigator,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: _seed,
        useMaterial3: true,
        brightness: Brightness.light,
      ),
      darkTheme: ThemeData(
        colorSchemeSeed: _seed,
        useMaterial3: true,
        brightness: Brightness.dark,
      ),
      themeMode: ThemeMode.system,
      home: HomeScreen(key: ObjectKey(services), services: services, auth: widget.accounts?.auth),
    );
  }
}
