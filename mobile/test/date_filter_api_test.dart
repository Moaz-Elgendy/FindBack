import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:findback/data/api_client.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('list and search send date separately without mutating caller filters',
      () async {
    FlutterSecureStorage.setMockInitialValues({});
    final requests = <RequestOptions>[];
    final api = ApiClient(
        dio: Dio()
          ..interceptors.add(InterceptorsWrapper(onRequest: (r, h) {
            requests.add(r);
            h.resolve(Response(
                requestOptions: r,
                statusCode: 200,
                data: r.path.endsWith('/search')
                    ? {'results': [], 'query': 'needle'}
                    : {'items': [], 'next_cursor': null}));
          })));
    final filters = {
      'saved_after': '2026-10-01T02:00:00+02:00',
      'type': 'video'
    };
    await api.listItems(filters: filters);
    await api.search('needle', filters: filters);
    for (final request in requests) {
      expect(
          request.uri.queryParameters['saved_after'], filters['saved_after']);
      expect(jsonDecode(request.uri.queryParameters['intelligence']!),
          {'type': 'video'});
    }
    expect(
        filters, {'saved_after': '2026-10-01T02:00:00+02:00', 'type': 'video'});
    api.close();
  });
}
