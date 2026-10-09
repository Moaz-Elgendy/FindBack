import 'package:findback/data/api_client.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('deleted saves leave the open queue while transient failures retry', () async {
    final pending = ['deleted', 'offline', 'valid'];
    final sync = SyncService(
      pending: () async => [],
      send: (_) async => throw UnimplementedError(),
      apply: (_) async {},
      markFailed: (_) async {},
      isOnline: () async => true,
      pendingOpens: () async => [...pending],
      sendOpen: (id) async {
        if (id == 'deleted') {
          throw ApiException('not found', statusCode: 404, kind: ApiFailureKind.rejected);
        }
        if (id == 'offline') throw ApiException('offline');
      },
      markOpenDone: (id) async => pending.remove(id),
    );
    await sync.flush();
    expect(pending, ['offline']);
    expect(sync.lastError.value!.kind, ApiFailureKind.connectivity);
    await sync.stop();
  });
}
