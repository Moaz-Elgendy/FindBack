import 'dart:convert';
import 'dart:async';
import 'dart:math';

import 'package:flutter/widgets.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../app_services.dart';
import '../config.dart';
import '../data/api_client.dart';
import '../data/local_db.dart';
import '../data/token_store.dart';
import 'auth_service.dart';

typedef ScopedServices = Future<AppServices> Function(String? scope, bool guest, TokenStore tokens);

class _BoundTokens extends TokenStore {
  _BoundTokens(this.readToken);
  final Future<String?> Function() readToken;
  @override
  Future<String?> read() => readToken();
}

class AccountCoordinator extends ChangeNotifier {
  AccountCoordinator._(this.auth, this._storage, this._factory, this._guestApi);
  final AuthService auth;
  final FlutterSecureStorage _storage;
  final ScopedServices _factory;
  final ApiClient _guestApi;
  late AppServices services;
  late String _guestScope, _legacyScope, _activeScope;
  String? _accountId;

  /// The account whose push scope is currently open, for the tap guard.
  String? _accountIdForScope;
  Future<void> _switching = Future.value();
  bool _closed = false;
  String? error;
  final _started = Completer<void>();
  Future<void> get ready => _started.future;

  Future<void> start() async {
    try {
      await services.startSync();
      await services.startShareHandling();
    } finally {
      if (!_started.isCompleted) _started.complete();
    }
  }

  static String _newGuest() => List.generate(16, (_) => Random.secure().nextInt(256)
      .toRadixString(16).padLeft(2, '0')).join();
  static bool _safeScope(String value) => RegExp(r'^[a-zA-Z0-9_-]{1,80}$').hasMatch(value);

  static Future<AccountCoordinator> create({AuthService? auth,
    FlutterSecureStorage? storage, ScopedServices? factory, ApiClient? guestApi}) async {
    final secure = storage ?? const FlutterSecureStorage();
    final service = auth ?? AuthService(storage: secure);
    await service.restore(); // Local storage only; never gates startup on a network request.
    // `_accountIdForScope` is set by `_open` immediately before the factory
    // runs, so the push registration knows which account this scope belongs to
    // and can refuse a note addressed to a different one.
    late final AccountCoordinator coordinator;
    final ScopedServices build = factory ?? (scope, guest, tokens) async =>
        AppServices.create(database: await LocalDb.open(scope: scope), tokens: tokens,
            guest: guest, accountId: coordinator._accountIdForScope);
    coordinator = AccountCoordinator._(service, secure, build, guestApi ?? ApiClient());
    final savedGuest = await secure.read(key: 'findback.guestScope');
    coordinator._guestScope = savedGuest != null && _safeScope(savedGuest) ? savedGuest : _newGuest();
    coordinator._legacyScope = await secure.read(key: 'findback.legacyGuestScope') ?? coordinator._guestScope;
    await secure.write(key: 'findback.guestScope', value: coordinator._guestScope);
    await secure.write(key: 'findback.legacyGuestScope', value: coordinator._legacyScope);
    coordinator._accountId = service.currentSession?.id;
    coordinator.services = await coordinator._open(coordinator._accountId);
    service.session.addListener(coordinator._changed);
    return coordinator;
  }

  Future<AppServices> _open(String? account) async {
    final identifier = account ?? _guestScope;
    if (!_safeScope(identifier)) throw const AuthException('Invalid account identity.');
    final scope = account == null ? 'guest-$identifier' : 'account-$identifier';
    _activeScope = scope;
    Future<String?>? issuing;
    ApiException? guestFailure;
    DateTime guestRetryAt = DateTime.fromMillisecondsSinceEpoch(0);
    Future<String?> guestToken() async {
      if (issuing != null) return issuing!;
      Future<String?> issue() async {
        final key = 'findback.guestToken.${Uri.parse(AppConfig.apiBaseUrl).origin}.$identifier';
        final retryError = guestFailure;
        if (retryError != null && DateTime.now().isBefore(guestRetryAt)) throw retryError;
        try {
          final stored = await _storage.read(key: key);
          final legacy = stored == null ? await _storage.read(key: 'findback.guestToken.$identifier') : null;
          final encoded = stored ?? legacy;
          Map? saved;
          if (encoded != null) {
            try {
              saved = jsonDecode(encoded) as Map;
              if (!DateTime.parse(saved['expires_at'] as String).isAfter(DateTime.now().add(const Duration(minutes: 1))) || saved['access_token'] is! String) saved = null;
            } on Object { saved = null; }
          }
          if (saved != null) {
            final token = saved['access_token'] as String;
            if (legacy == null) return token;
            try {
              final identity = await _guestApi.validateGuestSession(token);
              if (identity['is_guest'] == true) {
                await _storage.write(key: key, value: encoded);
                return token;
              }
            } on ApiException catch (failure) {
              if (failure.kind != ApiFailureKind.unauthorized) rethrow;
            }
          }
          final data = await _guestApi.createGuestSession();
          if (data['access_token'] is! String || data['expires_at'] is! String) {
            throw ApiException('Invalid guest session response', kind: ApiFailureKind.malformed);
          }
          await _storage.write(key: key, value: jsonEncode(data));
          return data['access_token'] as String;
        } on ApiException catch (failure) {
          guestFailure = failure;
          guestRetryAt = DateTime.now().add(Duration(seconds: failure.retryAfterSeconds ?? 30));
          rethrow;
        }
      }
      issuing = issue();
      try { return await issuing; } finally { issuing = null; }
    }
    final tokens = _BoundTokens(() async {
      if (_closed || _activeScope != scope || auth.currentSession?.id != account) {
        throw ApiException('This account session is no longer active.', kind: ApiFailureKind.unauthorized);
      }
      if (account == null) return guestToken();
      try {
        final token = await auth.accessTokenFor(account);
        if (token == null) throw ApiException('Sign in again to sync this account.', kind: ApiFailureKind.unauthorized);
        return token;
      } on AuthException catch (failure) {
        throw ApiException(failure.message, kind: failure.retryable
            ? ApiFailureKind.connectivity : ApiFailureKind.unauthorized, statusCode: failure.statusCode);
      }
    });
    // `accountId` lets the push registration refuse a note addressed to a
    // different account than the one signed in on this scope.
    _accountIdForScope = account;
    return _factory(account == null && identifier == _legacyScope ? null : scope, account == null, tokens);
  }

  void _changed() {
    final account = auth.currentSession?.id;
    AppServices? candidate;
    _switching = _switching.then((_) async {
      if (_closed || account == _accountId) return;
      final previous = services;
      await previous.stopAccountWork();
      await previous.share.dispose();
      final wasGuest = _accountId == null;
      if (account == null) {
        _guestScope = _newGuest(); // Signing out never exposes the signed-in cache to a guest.
        await _storage.write(key: 'findback.guestScope', value: _guestScope);
      }
      final next = await _open(account);
      candidate = next;
      if (wasGuest && account != null) {
        await next.db.importGuest(previous.db);
        next.initialLibrary = await next.db.recentLocalItems(limit: 20);
      }
      _accountId = account;
      services = next;
      candidate = null;
      error = null;
      notifyListeners();
      // The old screen must detach before its database is closed.
      await WidgetsBinding.instance.endOfFrame;
      await previous.dispose();
      await next.startSync();
      await next.startShareHandling();
    }).catchError((Object failure) async {
      error = 'Could not switch account storage. Your existing memories are preserved.';
      if (auth.currentSession?.id != _accountId) {
        if (candidate != null) await candidate!.dispose();
        await auth.signOut();
        await services.dispose();
        if (_accountId != null) {
          _guestScope = _newGuest();
          await _storage.write(key: 'findback.guestScope', value: _guestScope);
        }
        _accountId = null;
        services = await _open(null);
        notifyListeners();
        await WidgetsBinding.instance.endOfFrame;
        await services.startSync();
        await services.startShareHandling();
      } else {
        notifyListeners();
      }
    });
  }

  Future<void> get settled => _switching;

  Future<void> deleteAccount() async {
    final operation = _switching.then((_) async {
      final current = auth.currentSession;
      if (current == null || current.id != _accountId) {
        throw const AuthException('Sign in to delete your account.');
      }
      final previous = services;
      // Unregister the device BEFORE the account is deleted. The token delete
      // is owner-scoped, so once the user row is gone it answers 404 and the
      // only thing left removing the token is the database cascade. Doing it
      // first lets the app ask properly and lets the server report a refusal.
      //
      // A failure here must not stop the deletion: the cascade still removes
      // the token, and leaving an account undeleted is far worse.
      try {
        await previous.stopAccountWork();
      } on Object catch (_) {
        // Deliberately swallowed; see above.
      }
      try {
        await previous.api.deleteAccount();
      } catch (_) {
        // Deletion failed; this is still the active account and must keep working.
        await previous.startSync();
        await previous.startShareHandling();
        rethrow;
      }
      try {
        previous.api.close();
        await previous.db.clearAccountData();
      } finally {
        if (auth.currentSession?.id == current.id) await auth.signOut();
      }
    });
    _switching = operation.catchError((Object _) {});
    await operation;
    await settled;
  }

  Future<void> close() async {
    _closed = true;
    auth.session.removeListener(_changed);
    await _switching;
    await services.dispose();
    _guestApi.close();
    await auth.dispose();
    super.dispose();
  }
}

class AccountCoordinatorScope extends InheritedWidget {
  const AccountCoordinatorScope({super.key, required this.accounts, required super.child});
  final AccountCoordinator? accounts;
  static AccountCoordinator? maybeOf(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AccountCoordinatorScope>()?.accounts;
  @override bool updateShouldNotify(AccountCoordinatorScope oldWidget) => oldWidget.accounts != accounts;
}
