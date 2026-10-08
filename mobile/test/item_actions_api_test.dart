import 'package:dio/dio.dart';
import 'package:findback/data/api_client.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test(
      'item actions use the correct contracts and leave legacy delete permanent',
      () async {
    FlutterSecureStorage.setMockInitialValues({});
    final requests = <RequestOptions>[];
    final dio = Dio()
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
        requests.add(request);
        final noContent =
            request.method == 'DELETE' || request.path.endsWith('/restore');
        handler.resolve(Response(
            requestOptions: request,
            statusCode: noContent ? 204 : 200,
            data: noContent
                ? null
                : {
                    'id': 'memory',
                    'url': 'https://example.test',
                    'status': 'ready',
                    'title': 'Edited',
                    'summary': 'Edited brief',
                    'link_only': request.path.endsWith('/keep-link')
                  }));
      }));
    final api = ApiClient(dio: dio);
    await api.deleteItem('memory');
    await api.deleteUndoable('memory');
    await api.restoreItem('memory');
    final edited =
        await api.editItem('memory', title: 'Edited', summary: 'Edited brief');
    await api.retryItem('memory');
    final kept = await api.keepLinkOnly('memory');
    expect(requests.map((request) => request.method).toList(),
        ['DELETE', 'DELETE', 'POST', 'PATCH', 'POST', 'POST']);
    expect(requests[0].uri.queryParameters, isEmpty);
    expect(requests[1].uri.queryParameters, {'undoable': 'true'});
    expect(requests[2].path, endsWith('/items/memory/restore'));
    expect(requests[3].data, {'title': 'Edited', 'summary': 'Edited brief'});
    expect(requests[4].path, endsWith('/items/memory/retry'));
    expect(requests[5].path, endsWith('/items/memory/keep-link'));
    expect(edited.briefText, 'Edited brief');
    expect(kept.linkOnly, isTrue);
  });
}
