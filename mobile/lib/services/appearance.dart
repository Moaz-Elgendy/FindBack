import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class Appearance extends ChangeNotifier {
  Appearance({FlutterSecureStorage? storage})
      : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  static const _key = 'findback.appearance';
  ThemeMode _mode = ThemeMode.system;
  ThemeMode get mode => _mode;

  Future<void> restore() async {
    final saved = await _storage.read(key: _key);
    _mode = ThemeMode.values.firstWhere((mode) => mode.name == saved,
        orElse: () => ThemeMode.system);
    notifyListeners();
  }

  Future<void> select(ThemeMode mode) async {
    await _storage.write(key: _key, value: mode.name);
    _mode = mode;
    notifyListeners();
  }
}

class AppearanceScope extends InheritedNotifier<Appearance> {
  const AppearanceScope(
      {super.key, required Appearance appearance, required super.child})
      : super(notifier: appearance);

  static Appearance? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppearanceScope>()?.notifier;
}
