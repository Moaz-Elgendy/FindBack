import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';

import '../config.dart';
import '../models/item.dart';
import '../models/reminder.dart';
import '../models/weekly_note_settings.dart';
import '../models/memory_collection.dart';
import '../models/search_result.dart';
import 'token_store.dart';

/// Why a call failed, so callers can tell "no network" apart from "server said
/// no" — the offline queue must retry the first and surface the second.
enum ApiFailureKind { connectivity, timeout, unauthorized, server, rejected, malformed }

class ApiException implements Exception {
  /// The default kind is the *retryable* one: every site that constructs this
  /// from a real HTTP response names its kind explicitly, so the only things
  /// left relying on the default are hand-thrown errors, and for those the safe
  /// mistake is to keep the data and try again rather than treat it as final.
  ApiException(this.message, {this.statusCode, this.kind = ApiFailureKind.connectivity, this.retryAfterSeconds});

  final String message;
  final int? statusCode;
  final ApiFailureKind kind;
  final int? retryAfterSeconds;

  String get queuedMessage => statusCode == 429
      ? 'Saving is temporarily rate-limited. Your links are safe and will retry later.'
      : kind == ApiFailureKind.unauthorized
      ? 'Sign in again to upload your saved links.'
      : kind == ApiFailureKind.server
      ? 'The processing service is unavailable. Your links are safe and will retry.'
      : 'Cannot reach the processing service. Your links are safe and will retry.';

  bool get isRetryableOffline =>
      kind == ApiFailureKind.connectivity || kind == ApiFailureKind.timeout || kind == ApiFailureKind.server || statusCode == 429;

  @override
  String toString() => 'ApiException(${statusCode ?? kind.name}): $message';
}

/// Typed wrapper over the FindBack API. Everything is `Future`-based and throws
/// [ApiException]; no raw `Map` escapes to the UI.
class ApiClient {
  final processingError = ValueNotifier<ApiException?>(null);
  bool _closed = false;
  Future<Map<String, dynamic>> validateGuestSession(String token) => _send(() =>
      _dio.getUri<dynamic>(AppConfig.apiUri('/api/v1/auth/me'),
          options: Options(headers: {'Authorization': 'Bearer $token'})));

  Future<Map<String, dynamic>> createGuestSession() => _send(() =>
      _dio.postUri<dynamic>(AppConfig.apiUri('/api/v1/auth/guest')));
  ApiClient({Dio? dio, TokenStore? tokens})
      : _dio = dio ??
            Dio(
              BaseOptions(
                connectTimeout: const Duration(seconds: 10),
                receiveTimeout: const Duration(seconds: 20),
                // Accept all HTTP responses so _send can classify 4xx/5xx
                // consistently instead of Dio throwing before classification.
                validateStatus: (int? status) => status != null && status < 600,
              ),
            ) {
    _tokens = tokens ?? TokenStore();
  }

  static const int syncBatchSize = 20;

  final Dio _dio;
  late final TokenStore _tokens;
  final _feedCache = <(String, String?), ({String etag, Map<String, dynamic> body})>{};

  void close() {
    if (_closed) return;
    _closed = true;
    _dio.close(force: true);
    processingError.dispose();
  }

  Future<IngestResult> ingestUrl(String url, {String? preview, String? titleHint}) async {
    final data = await _post('/api/v1/ingest', <String, Object?>{
      'url': url,
      'preview': preview,
      'title_hint': titleHint,
    });
    return IngestResult.fromJson(data);
  }

  /// Uploads at most [syncBatchSize] queued captures; the caller owns the loop.
  Future<SyncBatchResult> syncBatch(List<SyncItem> items) async {
    final batch = items.length <= syncBatchSize ? items : items.sublist(0, syncBatchSize);
    final data = await _post('/api/v1/sync/batch', <String, Object?>{
      'items': batch.map((SyncItem item) => item.toJson()).toList(),
    });
    return SyncBatchResult.fromJson(data);
  }

  Future<SearchResponse> search(String query, {String? category, int limit = 10, Map<String, String>? filters}) async {
    final params = <String, String>{'q': query, 'limit': '$limit'};
    if (category != null && category.isNotEmpty && category != 'All') {
      params['category'] = category.toLowerCase();
    }
    final intelligence = Map<String, String>.from(filters?? {});
    final savedAfter = intelligence.remove('saved_after');
    if (savedAfter != null) params['saved_after'] = savedAfter;
    if (intelligence.isNotEmpty) params['intelligence'] = jsonEncode(intelligence);
    final data = await _get('/api/v1/search', params);
    return SearchResponse.fromJson(data);
  }

  Future<ItemPage> listItems({int limit = 20, String? cursor, String? category, Map<String, String>? filters}) async {
    final params = <String, String>{'limit': '$limit'};
    if (cursor != null && cursor.isNotEmpty) params['cursor'] = cursor;
    if (category != null && category.isNotEmpty && category != 'All') {
      params['category'] = category.toLowerCase();
    }
    final intelligence = Map<String, String>.from(filters?? {});
    final savedAfter = intelligence.remove('saved_after');
    if (savedAfter != null) params['saved_after'] = savedAfter;
    if (intelligence.isNotEmpty) params['intelligence'] = jsonEncode(intelligence);
    final data = await _send(() async {
      final uri = AppConfig.apiUri('/api/v1/items', params);
      final headers = await _headers(false);
      final key = (uri.toString(), headers['Authorization']);
      final cached = _feedCache[key];
      if (cached != null) headers['If-None-Match'] = cached.etag;
      final response = await _dio.getUri<dynamic>(uri, options: Options(headers: headers));
      if (response.statusCode == 304) {
        if (cached == null) {
          throw ApiException('The library refresh could not be read. Try again.',
              kind: ApiFailureKind.malformed);
        }
        response.data = cached.body;
      } else if (response.statusCode == 200 && response.data is Map) {
        final etag = response.headers.value('etag');
        if (etag == null) {
          _feedCache.remove(key);
        } else {
          _feedCache[key] = (etag: etag, body: Map<String, dynamic>.from(response.data as Map));
        }
      }
      return response;
    });
    final page = ItemPage.fromJson(data);
    ApiException? unavailable;
    if (page.items.any((item) => item.isGeneratingBrief)) {
      try {
        await _send(() => _dio.getUri<dynamic>(AppConfig.apiUri('/ready')));
      } on ApiException catch (error) {
        if (error.kind == ApiFailureKind.server) unavailable = error;
      }
    }
    if (!_closed) processingError.value = unavailable;
    return page;
  }

  Future<List<MemoryReminder>> listReminders() async {
    final data = await _get('/api/v1/reminders', const {});
    return (data['reminders'] as List).map((row) => MemoryReminder.fromJson(Map<String, dynamic>.from(row as Map))).toList();
  }

  Future<WeeklyNoteSettings> weeklyNoteSettings() async =>
      WeeklyNoteSettings.fromJson(await _get('/api/v1/account/weekly-note', const {}));

  Future<WeeklyNoteSettings> saveWeeklyNoteSettings(WeeklyNoteSettings value) async =>
      WeeklyNoteSettings.fromJson(await _send(() async => _dio.putUri<dynamic>(
        AppConfig.apiUri('/api/v1/account/weekly-note'), data: value.toJson(),
        options: Options(headers: await _headers(true)))));

  Future<Map<String, dynamic>> exportSaves() => _get('/api/v1/account/export', const {});
  Future<void> deleteAccount() => _delete('/api/v1/account');

  Future<void> setReminder(MemoryReminder value) async {
    await _send(() async => _dio.putUri<dynamic>(AppConfig.apiUri('/api/v1/items/${value.itemId}/reminder'),
      data: value.toJson(), options: Options(headers: await _headers(true))));
  }
  Future<void> removeReminder(String id) => _delete('/api/v1/items/$id/reminder');
  Future<void> acknowledgeReminder(MemoryReminder value) async {
    await _post('/api/v1/items/${value.itemId}/reminder/delivered', {'scheduled_at': value.scheduledAt.toUtc().toIso8601String()});
  }

  Future<ItemDetail> getItem(String id) async =>
      ItemDetail.fromJson(await _get('/api/v1/items/$id', const <String, String>{}));

  /// The saves one weekly note counted, for the "Worth another look" screen.
  ///
  /// The ids are the frozen set the notification was built from, not a
  /// recomputation, so the list matches the count on the lock screen. Both
  /// counts come back because a save deleted since the note was sent is omitted
  /// from `items`, and the screen has to be able to say so honestly.
  Future<SnapshotPage> snapshotItems(String id) async =>
      SnapshotPage.fromJson(await _get('/api/v1/snapshots/$id', const <String, String>{}));

  /// Tell the backend where to reach this device.
  ///
  /// Safe to call on every launch: the backend upserts on the token, so a
  /// repeated call neither duplicates the row nor fails. Registering a token
  /// that another account already holds moves it to this account, which is what
  /// makes a shared device follow whoever signed in to it.
  Future<void> registerDevice(String token, String platform) async {
    await _post('/api/v1/devices', {'token': token, 'platform': platform});
  }

  /// Forget this device, so it stops being notified for the signed-in account.
  ///
  /// Answers 404 when the token is unknown or belongs to another account; the
  /// caller treats that as already-gone rather than as a failure.
  Future<void> removeDevice(String token) =>
      _delete('/api/v1/devices/${Uri.encodeComponent(token)}');

  Future<List<MemoryCollection>> listCollections() async {
    final data = await _get('/api/v1/collections', const {});
    return (data['collections'] as List).map((c) => MemoryCollection.fromJson(Map<String, dynamic>.from(c as Map))).toList();
  }

  Future<MemoryCollection> saveCollection(MemoryCollection value) async => MemoryCollection.fromJson(
      await _send(() async => _dio.putUri<dynamic>(AppConfig.apiUri('/api/v1/collections/${value.id}'),
        data: value.toJson(), options: Options(headers: await _headers(true)))));

  Future<void> deleteCollection(String id) => _delete('/api/v1/collections/$id');

  Future<Map<String, dynamic>> createShare(String id) => _post('/api/v1/items/$id/share', const {});
  Future<ItemDetail> redeemShare(String token) async =>
      ItemDetail.fromJson(await _post('/api/v1/shares/$token/redeem', const {}));
  Future<Map<String, dynamic>> shareProfile() => _get('/api/v1/account/profile', const {});
  Future<void> saveDisplayName(String? name) async {
    await _send(() async => _dio.putUri<dynamic>(AppConfig.apiUri('/api/v1/account/profile'),
        data: {'display_name': name}, options: Options(headers: await _headers(true))));
  }
  Future<List<Map<String, dynamic>>> activeShares() async {
    final data = await _get('/api/v1/account/shares', const {});
    return (data['shares'] as List).map((v) => Map<String, dynamic>.from(v as Map)).toList();
  }
  Future<void> revokeShare(String id) => _delete('/api/v1/account/shares/$id');

  Future<void> deleteItem(String id) => _delete('/api/v1/items/$id');

  Future<void> deleteUndoable(String id) => _delete('/api/v1/items/$id', {'undoable': 'true'});

  Future<void> restoreItem(String id) async {
    await _post('/api/v1/items/$id/restore', const {});
  }

  Future<ItemDetail> editItem(String id, {required String title, required String summary}) async =>
      ItemDetail.fromJson(await _send(() async => _dio.patchUri<dynamic>(
        AppConfig.apiUri('/api/v1/items/$id'), data: {'title': title, 'summary': summary},
        options: Options(headers: await _headers(true)))));

  Future<ItemDetail> summarizeAgain(String id, {bool replaceEdits = false}) async =>
      ItemDetail.fromJson(await _post('/api/v1/items/$id/summarize-again', {'replace_edits': replaceEdits}));

  Future<ItemDetail> retryItem(String id) async =>
      ItemDetail.fromJson(await _post('/api/v1/items/$id/retry', const {}));

  Future<ItemDetail> keepLinkOnly(String id) async =>
      ItemDetail.fromJson(await _post('/api/v1/items/$id/keep-link', const {}));

  /// Tells the server this memory was opened. The endpoint stamps
  /// `first_opened_at` only while it is NULL, so repeat calls are harmless.
  Future<void> markOpened(String id) async {
    await _post('/api/v1/items/$id/open', const {});
  }

  // `AppConfig.apiUri` returns a `Uri`, so the calls go through dio's `*Uri`
  // variants — the plain `get`/`post`/`delete` take a `String` path.
  Future<Map<String, dynamic>> _get(String path, Map<String, String> params) => _send(() async {
        return _dio.getUri<dynamic>(
          AppConfig.apiUri(path, params),
          options: Options(headers: await _headers(false)),
        );
      });

  Future<Map<String, dynamic>> _post(String path, Map<String, Object?> body) => _send(() async {
        return _dio.postUri<dynamic>(
          AppConfig.apiUri(path),
          data: body,
          options: Options(headers: await _headers(true)),
        );
      });

  Future<void> _delete(String path, [Map<String, String> params = const {}]) async {
    await _send(() async {
      return _dio.deleteUri<dynamic>(
        AppConfig.apiUri(path, params),
        options: Options(headers: await _headers(false)),
      );
    });
  }

  Future<Map<String, dynamic>> _send(Future<Response<dynamic>> Function() request) async {
    final Response<dynamic> response;
    try {
      response = await request();
    } on DioException catch (error) {
      throw _classify(error);
    } on FormatException catch (error) {
      throw ApiException('malformed request: ${error.message}', kind: ApiFailureKind.malformed);
    }

    final status = response.statusCode;
    if (status == null || status >= 400) {
      final detail = response.data is Map && response.data['detail'] is String
          ? response.data['detail'] as String
          : 'request failed with HTTP ${status ?? 'unknown'}';
      throw ApiException(
        detail,
        statusCode: status,
        retryAfterSeconds: int.tryParse(response.headers.value('retry-after') ?? ''),
        kind: status == 401 || status == 403
            ? ApiFailureKind.unauthorized
            : status != null && status >= 500
                ? ApiFailureKind.server
                : ApiFailureKind.rejected,
      );
    }
    final data = response.data;
    if (data == null) return <String, dynamic>{}; // 204 No Content
    if (data is Map) return Map<String, dynamic>.from(data);
    throw ApiException('expected a JSON object, got ${data.runtimeType}',
        statusCode: status, kind: ApiFailureKind.malformed);
  }

  ApiException _classify(DioException error) {
    final status = error.response?.statusCode;
    final kind = switch (error.type) {
      DioExceptionType.connectionError || DioExceptionType.connectionTimeout => ApiFailureKind.connectivity,
      DioExceptionType.receiveTimeout || DioExceptionType.sendTimeout => ApiFailureKind.timeout,
      _ => status == null
          ? ApiFailureKind.connectivity
          : status == 401 || status == 403
              ? ApiFailureKind.unauthorized
              : status >= 500
                  ? ApiFailureKind.server
                  : ApiFailureKind.rejected,
    };
    return ApiException(_detail(error) ?? error.message ?? error.type.name, statusCode: status, kind: kind,
        retryAfterSeconds: int.tryParse(error.response?.headers.value('retry-after') ?? ''));
  }

  String? _detail(DioException error) {
    final data = error.response?.data;
    if (data is Map && data['detail'] is String) return data['detail'] as String;
    return null;
  }

  Future<Map<String, String>> _headers(bool json) async {
    final token = await _tokens.read();
    return <String, String>{
      if (json) 'Content-Type': 'application/json',
      if (token != null && token.isNotEmpty) 'Authorization': 'Bearer $token',
    };
  }
}
