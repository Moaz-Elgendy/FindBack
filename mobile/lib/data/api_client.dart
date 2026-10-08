import 'dart:convert';
import 'package:dio/dio.dart';

import '../config.dart';
import '../models/item.dart';
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
  ApiException(this.message, {this.statusCode, this.kind = ApiFailureKind.connectivity});

  final String message;
  final int? statusCode;
  final ApiFailureKind kind;

  bool get isRetryableOffline =>
      kind == ApiFailureKind.connectivity || kind == ApiFailureKind.timeout || kind == ApiFailureKind.server || statusCode == 429;

  @override
  String toString() => 'ApiException(${statusCode ?? kind.name}): $message';
}

/// Typed wrapper over the FindBack API. Everything is `Future`-based and throws
/// [ApiException]; no raw `Map` escapes to the UI.
class ApiClient {
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

  void close() => _dio.close(force: true);

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
    if (filters?.isNotEmpty == true) params['intelligence'] = jsonEncode(filters);
    final data = await _get('/api/v1/search', params);
    return SearchResponse.fromJson(data);
  }

  Future<ItemPage> listItems({int limit = 20, String? cursor, String? category, Map<String, String>? filters}) async {
    final params = <String, String>{'limit': '$limit'};
    if (cursor != null && cursor.isNotEmpty) params['cursor'] = cursor;
    if (category != null && category.isNotEmpty && category != 'All') {
      params['category'] = category.toLowerCase();
    }
    if (filters?.isNotEmpty == true) params['intelligence'] = jsonEncode(filters);
    final data = await _get('/api/v1/items', params);
    return ItemPage.fromJson(data);
  }

  Future<ItemDetail> getItem(String id) async =>
      ItemDetail.fromJson(await _get('/api/v1/items/$id', const <String, String>{}));

  Future<List<MemoryCollection>> listCollections() async {
    final data = await _get('/api/v1/collections', const {});
    return (data['collections'] as List).map((c) => MemoryCollection.fromJson(Map<String, dynamic>.from(c as Map))).toList();
  }

  Future<MemoryCollection> saveCollection(MemoryCollection value) async => MemoryCollection.fromJson(
      await _send(() async => _dio.putUri<dynamic>(AppConfig.apiUri('/api/v1/collections/${value.id}'),
        data: value.toJson(), options: Options(headers: await _headers(true)))));

  Future<void> deleteCollection(String id) => _delete('/api/v1/collections/$id');

  Future<void> deleteItem(String id) => _delete('/api/v1/items/$id');

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

  Future<void> _delete(String path) async {
    await _send(() async {
      return _dio.deleteUri<dynamic>(
        AppConfig.apiUri(path),
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
    return ApiException(_detail(error) ?? error.message ?? error.type.name, statusCode: status, kind: kind);
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
