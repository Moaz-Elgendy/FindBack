import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../config.dart';

class AuthSession {
  const AuthSession(
      {required this.id,
      required this.email,
      required this.accessToken,
      required this.refreshToken,
      required this.expiresAt});
  final String id, email, accessToken, refreshToken;
  final DateTime expiresAt;
  Map<String, dynamic> toJson() => {
        'id': id,
        'email': email,
        'access_token': accessToken,
        'refresh_token': refreshToken,
        'expires_at': expiresAt.millisecondsSinceEpoch ~/ 1000
      };
}

class AuthException implements Exception {
  const AuthException(this.message,
      {this.invalidSession = false, this.retryable = false, this.statusCode});
  final String message;
  final bool invalidSession;
  final bool retryable;
  final int? statusCode;
  @override
  String toString() => message;
}

class AuthService {
  AuthService(
      {Dio? dio,
      FlutterSecureStorage? storage,
      String? supabaseUrl,
      String? publicKey})
      : _dio = dio ??
            Dio(BaseOptions(
                connectTimeout: const Duration(seconds: 15),
                receiveTimeout: const Duration(seconds: 15))),
        _storage = storage ?? const FlutterSecureStorage(),
        _url = supabaseUrl ?? AppConfig.supabaseUrl,
        _key = publicKey ?? AppConfig.supabaseAnonKey;
  static const _storageKey = 'findback.authSession';
  final Dio _dio;
  final FlutterSecureStorage _storage;
  final String _url, _key;
  final session = ValueNotifier<AuthSession?>(null);
  AuthSession? get currentSession => session.value;
  int _generation = 0;
  Future<String?>? _refreshing;
  Future<void> _storageOperation = Future<void>.value();

  Future<void> _store(Future<void> Function() action) {
    final operation = _storageOperation.then((_) => action());
    _storageOperation = operation.catchError((Object _) {});
    return operation;
  }

  Future<void> dispose() async {
    ++_generation;
    _dio.close(force: true);
    await _storageOperation;
    session.dispose();
  }

  static String validateEmail(String value) {
    final email = value.trim().toLowerCase();
    if (!RegExp(r'^[^\s@]+@[^\s@]+\.[^\s@]+$').hasMatch(email)) {
      throw const AuthException('Enter a valid email address.');
    }
    return email;
  }

  static void validatePassword(String value) {
    if (value.length < 6) {
      throw const AuthException('Password must have at least 6 characters.');
    }
  }

  Future<Map<String, dynamic>> _request(String method, String path,
      {Map<String, dynamic>? data,
      String? token,
      Map<String, dynamic>? query}) async {
    final base = Uri.tryParse(_url);
    if (base == null ||
        base.scheme != 'https' ||
        base.host.isEmpty ||
        base.userInfo.isNotEmpty ||
        _key.isEmpty) {
      throw const AuthException('Account sign-in is not configured.');
    }
    try {
      final response = await _dio.request<Map<String, dynamic>>(
          '${_url.replaceFirst(RegExp(r'/$'), '')}/auth/v1/$path',
          data: data,
          queryParameters: query,
          options: Options(method: method, followRedirects: false, headers: {
            'apikey': _key,
            if (token != null) 'Authorization': 'Bearer $token',
          }));
      if (response.data == null) {
        throw const AuthException('Invalid account response.',
            invalidSession: true);
    }
      return response.data!;
    } on DioException catch (error) {
      final status = error.response?.statusCode;
      if (status == 400 || status == 401 || status == 422) {
        throw const AuthException(
            'Check your email and password, or use password reset.',
            invalidSession: true);
      }
      throw AuthException(
          'Could not reach the account service. Please try again.',
          retryable: status == null || status >= 500 || status == 429,
          statusCode: status);
    }
  }

  AuthSession _parse(Map<String, dynamic> data, {Map<String, dynamic>? user}) {
    final identity = user ?? data['user'];
    final access = data['access_token'], refresh = data['refresh_token'];
    if (identity is! Map ||
        identity['id'] is! String ||
        (identity['id'] as String).isEmpty ||
        identity['email'] is! String ||
        access is! String ||
        access.isEmpty ||
        refresh is! String ||
        refresh.isEmpty) {
      throw const AuthException('Invalid account response.',
          invalidSession: true);
    }
    final seconds = data['expires_at'] is num
        ? (data['expires_at'] as num).toInt()
        : DateTime.now().millisecondsSinceEpoch ~/ 1000 +
            ((data['expires_in'] as num?)?.toInt() ?? 3600);
    return AuthSession(
        id: identity['id'] as String,
        email: validateEmail(identity['email'] as String),
        accessToken: access,
        refreshToken: refresh,
        expiresAt: DateTime.fromMillisecondsSinceEpoch(seconds * 1000));
  }

  Future<void> _adopt(AuthSession value, int generation) => _store(() async {
        if (_generation != generation) return;
        await _storage.write(
            key: _storageKey, value: jsonEncode(value.toJson()));
        if (_generation == generation) session.value = value;
      });

  Future<void> restore() async {
    final generation = ++_generation;
    _refreshing = null;
    try {
      final raw = await _storage.read(key: _storageKey);
      if (raw == null) return;
      final data = Map<String, dynamic>.from(jsonDecode(raw) as Map);
      final saved =
          _parse(data, user: {'id': data['id'], 'email': data['email']});
      if (_generation != generation) return;
      session.value = saved;
    } on AuthException {
      await signOut();
    } on FormatException {
      await signOut();
    } on TypeError {
      await signOut();
    }
  }

  Future<void> signIn(String email, String password) async {
    final normalized = validateEmail(email);
    validatePassword(password);
    final generation = ++_generation;
    _refreshing = null;
    final data = await _request('POST', 'token',
        query: {'grant_type': 'password'},
        data: {'email': normalized, 'password': password});
    await _adopt(_parse(data), generation);
  }

  Future<String> signUp(String email, String password) async {
    final normalized = validateEmail(email);
    validatePassword(password);
    final generation = ++_generation;
    _refreshing = null;
    final data = await _request('POST', 'signup',
        query: {'redirect_to': AppConfig.apiUri('/auth/confirmed').toString()},
        data: {'email': normalized, 'password': password});
    final user = data['user'] ?? data;
    if (user is Map &&
        user['identities'] is List &&
        (user['identities'] as List).isEmpty) {
      return 'This email may already be registered. Sign in or reset your password.';
    }
    if (data['access_token'] is String) {
      await _adopt(_parse(data), generation);
      return 'Signed in.';
    }
    return 'Check your email to confirm your account, then sign in.';
  }

  Future<void> signOut() async {
    ++_generation;
    _refreshing = null;
    session.value = null;
    await _store(() => _storage.delete(key: _storageKey));
  }

  Future<void> sendPasswordReset(String email) async {
    await _request('POST', 'recover',
        query: {'redirect_to': 'findback://auth/recovery'},
        data: {'email': validateEmail(email)});
  }

  Future<String?> accessTokenFor(String subject) async {
    final value = session.value;
    if (value == null || value.id != subject) return null;
    if (value.expiresAt
        .isAfter(DateTime.now().add(const Duration(seconds: 30)))) {
      return value.accessToken;
    }
    final refreshing = _refreshing ??= _refresh(value);
    try {
      await refreshing;
    } finally {
      if (identical(_refreshing, refreshing)) _refreshing = null;
    }
    return session.value?.id == subject ? session.value?.accessToken : null;
  }

  Future<String?> _refresh(AuthSession value) async {
    final generation = _generation;
    try {
      final data = await _request('POST', 'token',
          query: {'grant_type': 'refresh_token'},
          data: {'refresh_token': value.refreshToken});
      final refreshed = _parse(data);
      if (refreshed.id != value.id) {
        throw const AuthException('Account session changed.',
            invalidSession: true);
    }
      await _adopt(refreshed, generation);
      return refreshed.accessToken;
    } on AuthException catch (error) {
      if (!error.invalidSession) rethrow;
      if (_generation == generation) await signOut();
      return null;
    }
  }

  Future<bool> handleRecoveryLink(String link) async {
    final uri = Uri.tryParse(link);
    if (uri == null ||
        uri.scheme != 'findback' ||
        uri.host != 'auth' ||
        uri.path != '/recovery' ||
        uri.userInfo.isNotEmpty ||
        uri.hasPort ||
        uri.query.isNotEmpty) {
      return false;
    }
    Map<String, String> values;
    try {
      values = Uri.splitQueryString(uri.fragment);
    } on FormatException {
      return false;
    }
    final access = values['access_token'], refresh = values['refresh_token'];
    if (values['type'] != 'recovery' ||
        access == null ||
        access.isEmpty ||
        refresh == null ||
        refresh.isEmpty) {
      return false;
    }
    final generation = ++_generation;
    _refreshing = null;
    final user = await _request('GET', 'user', token: access);
    final data = <String, dynamic>{
      ...values,
      'expires_in': int.tryParse(values['expires_in'] ?? '') ?? 3600
    };
    await _adopt(_parse(data, user: user), generation);
    return _generation == generation;
  }

  Future<void> updatePassword(String password) async {
    validatePassword(password);
    final value = session.value;
    if (value == null) {
      throw const AuthException('Open a valid password reset link first.');
    }
    final token = await accessTokenFor(value.id);
    if (token == null) {
      throw const AuthException(
          'Your reset link expired. Request another link.');
    }
    await _request('PUT', 'user', token: token, data: {'password': password});
  }
}
