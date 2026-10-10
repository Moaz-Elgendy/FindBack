import 'package:dio/dio.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/token_store.dart';
import 'package:flutter_test/flutter_test.dart';

class _Tokens extends TokenStore {
  String token = 'account-a';
  @override
  Future<String?> read() async => token;
}

void main() {
  test('processing feed retains cards and reports worker outage even on unchanged refresh', () async {
    var healthy = false;
    final paths = <String>[];
    final api = ApiClient(tokens: _Tokens(), dio: Dio()..interceptors.add(
      InterceptorsWrapper(onRequest: (request, handler) {
        paths.add(request.path);
        if (request.path.endsWith('/ready')) {
          handler.resolve(Response(requestOptions: request, statusCode: healthy ? 200 : 503,
            data: {'status': healthy ? 'ok' : 'degraded', 'worker': healthy ? 'ok' : 'down'}));
        } else {
          handler.resolve(Response(requestOptions: request, statusCode: healthy ? 304 : 200,
            headers: Headers.fromMap({'etag': ['"reading"']}), data: healthy ? null : {
              'items': [{'id': 'reading', 'url': 'https://example.test', 'status': 'processing'}]}));
        }
      })));
    try {
      expect((await api.listItems()).items.single.id, 'reading');
      expect(api.processingError.value?.statusCode, 503);
      healthy = true;
      expect((await api.listItems()).items.single.id, 'reading');
      expect(api.processingError.value, isNull);
      expect(paths.where((path) => path.endsWith('/ready')), hasLength(2));
    } finally { api.close(); }
  });

  test('feed validators reuse content and stay scoped to account and query',
      () async {
    final tokens = _Tokens();
    final requests = <RequestOptions>[];
    final api = ApiClient(
        tokens: tokens,
        dio: Dio()
          ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
            requests.add(request);
            final unchanged = requests.length == 2;
            handler.resolve(Response(
              requestOptions: request,
              statusCode: unchanged ? 304 : 200,
              headers: Headers.fromMap({
                'etag': ['"version-1"']
              }),
              data: unchanged
                  ? null
                  : {
                      'items': [
                        {
                          'id': tokens.token,
                          'url': 'https://example.com/article',
                          'title': tokens.token,
                          'status': 'ready',
                        }
                      ],
                      'next_cursor': null,
                    },
            ));
          })));
    try {
      expect((await api.listItems()).items.single.id, 'account-a');
      expect((await api.listItems()).items.single.id, 'account-a');
      expect(requests[1].headers['If-None-Match'], '"version-1"');
      await api.listItems(category: 'recipe');
      expect(requests[2].headers['If-None-Match'], isNull);
      tokens.token = 'account-b';
      expect((await api.listItems()).items.single.id, 'account-b');
      expect(requests[3].headers['If-None-Match'], isNull);
    } finally {
      api.close();
    }
  });
}
