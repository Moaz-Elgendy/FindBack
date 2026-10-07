import 'dart:convert';
import 'dart:async';
import 'dart:math';

import 'package:dio/dio.dart';
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
  AccountCoordinator._(this.auth, this._storage, this._factory);
  final AuthService auth;
  final FlutterSecureStorage _storage;
  final ScopedServices _factory;
  late AppServices services;
  late String _guestScope, _legacyScope, _activeScope;
  String? _accountId;
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
    FlutterSecureStorage? storage, ScopedServices? factory}) async {
    final secure = storage ?? const FlutterSecureStorage();
    final service = auth ?? AuthService(storage: secure);
    await service.restore(); // Local storage only; never gates startup on a network request.
    final coordinator = AccountCoordinator._(service, secure, factory ?? (scope, guest, tokens) async =>
        AppServices.create(database: await LocalDb.open(scope: scope), tokens: tokens, guest: guest));
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
    Future<String?> guestToken() async {
      if (issuing != null) return issuing!;
      Future<String?> issue() async {
        final key = 'findback.guestToken.$identifier';
        final stored = await _storage.read(key: key);
        if (stored != null) {
          try {
            final saved = jsonDecode(stored) as Map;
            if (DateTime.parse(saved['expires_at'] as String).isAfter(DateTime.now().add(const Duration(minutes: 1)))) {
              return saved['access_token'] as String;
            }
          } on Object {
            // An invalid or expired local lease is replaced, not shared with another scope.
          }
        }
        final dio = Dio(BaseOptions(connectTimeout: const Duration(seconds: 10), receiveTimeout: const Duration(seconds: 20)));
        try {
          final response = await dio.post<Map<String, dynamic>>('${AppConfig.apiBaseUrl}/api/v1/auth/guest');
          final data = response.data;
          if (data == null || data['access_token'] is! String || data['expires_at'] is! String) {
            throw ApiException('Invalid guest session response', kind: ApiFailureKind.malformed);
          }
          await _storage.write(key: key, value: jsonEncode(data));
          return data['access_token'] as String;
        } on DioException catch (failure) {
          throw ApiException('Guest processing is temporarily unavailable.',
              kind: ApiFailureKind.connectivity, statusCode: failure.response?.statusCode);
        } finally {
          dio.close();
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
      if (wasGuest && account != null) await next.db.importGuest(previous.db);
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

  Future<void> close() async {
    _closed = true;
    auth.session.removeListener(_changed);
    await _switching;
    await services.dispose();
    await auth.dispose();
    super.dispose();
  }
}
