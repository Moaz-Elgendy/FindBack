/// Build-time configuration.
///
/// Values come from `--dart-define`, not from `.env` at runtime: a compiled
/// mobile binary has no .env to read, and baking secrets in is the classic
/// mobile leak. Everything here is safe to ship (public API base URL, Supabase
/// *public* anon key). See `mobile/README.md` for the exact `flutter run` line.
class AppConfig {
  const AppConfig._();

  /// FindBack API root, no trailing slash.
  static const String apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000',
  );

  /// Supabase project URL, empty until auth is wired up.
  static const String supabaseUrl = String.fromEnvironment('SUPABASE_URL');

  /// Supabase public anon key (never the service role key).
  static const String supabaseAnonKey = String.fromEnvironment('SUPABASE_PUBLISHABLE_KEY',
      defaultValue: String.fromEnvironment('SUPABASE_ANON_KEY'));

  /// Optional bearer token for pointing the app at a backend that runs with
  /// `DEV_AUTH_ENABLED=false`. Ignored when a token is already in secure storage.
  static const String devAccessToken = String.fromEnvironment('API_TOKEN');

  static bool get authConfigured =>
      supabaseUrl.isNotEmpty && supabaseAnonKey.isNotEmpty;

  /// `apiBaseUrl` + `path`, with `query` appended. Keeps every call site honest
  /// about whether a trailing slash belongs on the base URL.
  static Uri apiUri(String path, [Map<String, String>? query]) {
    final base = Uri.parse(apiBaseUrl);
    final normalizedPath = path.startsWith('/') ? path : '/$path';
    return base.replace(
      path: '${base.path}$normalizedPath',
      queryParameters: query == null || query.isEmpty ? null : query,
    );
  }
}
