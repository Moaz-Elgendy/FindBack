import 'package:dio/dio.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/models/weekly_note_settings.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  test('Account API uses persisted weekly settings, owner export and account deletion paths', () async {
    FlutterSecureStorage.setMockInitialValues({'findback.accessToken': 'token'});
    final requests = <RequestOptions>[];
    final dio = Dio()..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) {
      requests.add(request);
      handler.resolve(Response(requestOptions: request, statusCode: request.method == 'DELETE' ? 204 : 200,
        data: request.method == 'DELETE' ? null : request.path.endsWith('/export')
          ? {'version': 1, 'saves': []} : request.data ?? const WeeklyNoteSettings().toJson()));
    }));
    final api = ApiClient(dio: dio);
    expect((await api.weeklyNoteSettings()).weekday, 6);
    const choice = WeeklyNoteSettings(enabled: true, weekday: 0, hour: 9, minute: 30, timeZone: 'Africa/Cairo');
    expect((await api.saveWeeklyNoteSettings(choice)).toJson(), choice.toJson());
    expect((await api.exportSaves())['version'], 1);
    await api.deleteAccount();
    expect(requests.map((r) => r.method), ['GET', 'PUT', 'GET', 'DELETE']);
    expect(requests.map((r) => r.uri.path), [
      '/api/v1/account/weekly-note', '/api/v1/account/weekly-note', '/api/v1/account/export', '/api/v1/account']);
    expect(requests[1].data, choice.toJson());
    api.close();
  });
}
