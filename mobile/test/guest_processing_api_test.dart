import 'package:dio/dio.dart';
import 'package:findback/config.dart';
import 'package:findback/data/api_client.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('ordinary phone builds use the hosted HTTPS backend', () {
    expect(AppConfig.apiBaseUrl, 'https://findback.duckdns.org');
  });

  for (final status in [401, 429, 503]) {
    test('guest HTTP $status keeps its real failure category', () async {
      final dio = Dio()..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        expect(request.uri.path, '/api/v1/auth/guest');
        expect(request.headers.containsKey('Authorization'), isFalse);
        handler.resolve(Response(requestOptions: request, statusCode: status,
            data: {'detail': 'Guest service unavailable'},
            headers: Headers.fromMap({'retry-after': ['120']})));
      }));
      final api = ApiClient(dio: dio);
      addTearDown(api.close);
      await expectLater(api.createGuestSession(), throwsA(isA<ApiException>()
          .having((e) => e.statusCode, 'status', status)
          .having((e) => e.kind, 'kind', status == 401 ? ApiFailureKind.unauthorized
              : status == 503 ? ApiFailureKind.server : ApiFailureKind.rejected)
          .having((e) => e.retryAfterSeconds, 'retry delay', 120)));
    });
  }
}
