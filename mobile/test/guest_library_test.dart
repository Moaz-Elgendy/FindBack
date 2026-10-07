import 'dart:async';
import 'dart:io';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class GuestApi extends ApiClient {
  GuestApi(this.db);
  final LocalDb db;
  int reads = 0, deletes = 0;
  @override
  Future<ItemDetail> getItem(String id) async {
    reads++;
    throw ApiException('expired', statusCode: 404, kind: ApiFailureKind.rejected);
  }
  @override
  Future<void> deleteItem(String id) async {
    expect((await db.localItem(id))!.instantBrief, 'A useful Brief.');
    deletes++;
  }
  @override
  Future<IngestResult> ingestUrl(String url, {String? preview, String? titleHint}) async =>
      IngestResult(id: 'new-stage', status: 'pending', canonicalUrl: url);
}
ItemDetail memory(String id, {bool complete = true}) => ItemDetail.fromJson({
  'id': id, 'url': 'https://example.test/$id', 'title_clean': 'Claude skills',
  'status': complete ? 'ready' : 'processing', 'brief_source': complete ? 'llm' : 'fallback',
  'instant_brief': complete ? 'A useful Brief.' : 'Draft',
  'created_at': '2026-09-29T10:00:00Z', 'tags': ['claude skills'],
  'key_points': [{'text': 'Install a skill.', 'source_ref': '00:39'}],
});
void main() {
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi; });
  test('guest cache precedes deletion and reads stay local', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = GuestApi(db);
    final library = GuestLibrary(db, api);
    await GuestLibrary(db, api).cacheAndRelease(memory('ready'));
    expect(api.deletes, 1);
    expect((await library.read('ready')).instantBrief, 'A useful Brief.');
    expect((await library.items.recent()).single.id, 'ready');
    expect(api.reads, 0);
    api.close(); await db.close();
  });
  test('expired staging retries URL and replaces stale id', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = GuestApi(db);
    final old = memory('expired', complete: false);
    await db.upsertRemoteItems([old]);
    final result = await GuestLibrary(db, api).read(old.id);
    expect(result.id, 'new-stage'); expect(result.url, old.url);
    expect(await db.localItem(old.id), isNull); expect(api.deletes, 0);
    api.close(); await db.close();
  });
  test('import preserves guest data and isolates account caches', () async {
    final guest = await LocalDb.openAt(inMemoryDatabasePath);
    final directory = await Directory.systemTemp.createTemp('findback-account-test');
    final a = await LocalDb.openAt('${directory.path}/a.db');
    final b = await LocalDb.openAt('${directory.path}/b.db');
    final original = memory('guest');
    await guest.upsertRemoteItems([original]);
    await a.importGuest(guest); await a.importGuest(guest);
    expect(await a.pendingCount(), 1);
    final copy = (await a.localItem((await a.recentLocalItems()).single.id))!;
    expect(copy.instantBrief, original.instantBrief); expect(copy.keyPoints, original.keyPoints);
    expect(copy.tags, original.tags); expect(copy.createdAt, original.createdAt);
    expect((await guest.localItem('guest'))!.status, 'ready');
    expect(await b.recentLocalItems(), isEmpty);
    await guest.close(); await a.close(); await b.close();
    await directory.delete(recursive: true);
  });
  test('sync stop awaits in-flight mapping', () async {
    final response = Completer<SyncBatchResult>(), entered = Completer<void>();
    var applied = false, stopped = false;
    final sync = SyncService(pending: () async => [SyncItem(clientId: 'one', url: 'https://example.test', capturedAt: '2026-09-29')],
      send: (_) { entered.complete(); return response.future; },
      apply: (_) async { applied = true; }, markFailed: (_) async {}, isOnline: () async => true);
    final flush = sync.flush(); await entered.future;
    final stop = sync.stop().then((_) { stopped = true; });
    await Future<void>.delayed(Duration.zero); expect(stopped, isFalse);
    response.complete(const SyncBatchResult(mapped: [], failedClientIds: []));
    await flush; await stop; expect(applied, isTrue); expect(stopped, isTrue);
  });
}
