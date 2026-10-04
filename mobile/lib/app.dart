import 'package:flutter/material.dart';

import 'app_services.dart';
import 'features/home/home_screen.dart';

/// Root widget. Theme only — dependencies arrive through [AppServices].
class FindBackApp extends StatelessWidget {
  const FindBackApp({super.key, required this.services});

  final AppServices services;

  /// The RN client's accent (`#1769aa`) kept as the seed so the brand survives
  /// the rewrite.
  static const Color _seed = Color(0xFF1769AA);

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'FindBack',
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
      home: HomeScreen(services: services),
    );
  }
}
