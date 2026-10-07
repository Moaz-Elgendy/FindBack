import 'dart:io';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi; });

  test('restored backend can be retried immediately without losing queued links', () async {
    var reachable = false;
    var sends = 0;
    final queue = [const SyncItem(clientId: 'a', url: 'https://example.test/a', capturedAt: '2026-10-06')];
    final sync = SyncService(pending: () async => queue,
      send: (_) async {
        sends++;
        if (!reachable) throw ApiException('connection refused');
        return const SyncBatchResult(mapped: [MappedSave(clientId: 'a', serverId: 'server-a')], failedClientIds: []);
      }, apply: (_) async => queue.clear(), markFailed: (_) async {},
      isOnline: () async => true, retryBase: const Duration(hours: 1));
    await sync.flush();
    expect(queue, hasLength(1));
    expect(sync.lastError.value, isNotNull);
    reachable = true;
    await sync.flush();
    expect(sends, 1, reason: 'automatic retry retains backoff');
    expect(await sync.flush(force: true), 1);
    expect(queue, isEmpty);
    expect(sync.lastError.value, isNull);
  });

  test('forced retries still respect the single uploader lock', () async {
    var sends = 0;
    final sync = SyncService(pending: () async => [const SyncItem(clientId: 'a', url: 'https://x/a', capturedAt: 'now')],
      send: (_) async { sends++; return const SyncBatchResult(mapped: [], failedClientIds: []); },
      apply: (_) async {}, markFailed: (_) async {}, isOnline: () async => true);
    await Future.wait([sync.flush(force: true), sync.flush(force: true)]);
    expect(sends, 1);
  });

  test('an opened queued memory resolves to its server ID after sync and restart', () async {
    final dir = await Directory.systemTemp.createTemp('findback-queue-');
    final path = '${dir.path}/queue.db';
    var db = await LocalDb.openAt(path);
    final client = await db.queueSave(url: 'https://example.test/queued');
    await db.applyMapped([MappedSave(clientId: client, serverId: 'server-a')]);
    expect(await db.localItem('local-$client'), isNull, reason: 'no ghost row');
    await db.close();
    db = await LocalDb.openAt(path);
    try {
      final service = ItemsService(
        remoteItem: (id) async => ItemDetail.fromJson({'id': id, 'url': 'https://example.test/queued', 'status': 'ready'}),
        localItem: db.localItem, resolveLocalId: db.syncedItemId,
        remoteRecent: (_, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []),
        localRecent: (_, {String? category, Map<String, String>? filters}) async => [], cache: db.upsertRemoteItems,
        remoteDelete: (_) async {}, localDelete: db.deleteItem, dropQueued: db.dropQueued,
        isOnline: () async => true);
      expect((await service.getItem('local-$client'))?.id, 'server-a');
      expect((await service.getItem('local-$client'))?.isReady, isTrue);
      expect(await db.pendingQueue(), isEmpty);
      expect((await db.db.query('sync_queue')).single['server_id'], 'server-a');
    } finally { await db.close(); await dir.delete(recursive: true); }
  });
}
