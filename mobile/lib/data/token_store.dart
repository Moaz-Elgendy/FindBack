import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../config.dart';

/// Where the bearer token lives: Keychain on iOS, encrypted prefs on Android.
///
/// The React Native client had the same storage but no way to seed it, so the
/// app could only talk to a backend running `DEV_AUTH_ENABLED=true`. `API_TOKEN`
/// (a `--dart-define`) keeps that dev path working without hardcoding a secret
/// in source.
class TokenStore {
  TokenStore({FlutterSecureStorage? storage})
      : _storage = storage ??
            const FlutterSecureStorage(
              aOptions: AndroidOptions(encryptedSharedPreferences: true),
            );

  static const String _key = 'findback.accessToken';

  final FlutterSecureStorage _storage;

  Future<String?> read() async {
    final stored = await _storage.read(key: _key);
    if (stored != null && stored.isNotEmpty) return stored;
    return AppConfig.devAccessToken.isEmpty ? null : AppConfig.devAccessToken;
  }

  Future<void> write(String token) => _storage.write(key: _key, value: token);

  Future<void> clear() => _storage.delete(key: _key);
}
