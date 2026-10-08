import 'dart:io';
import 'dart:async';
import 'package:dio/dio.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/memory_actions.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

ItemDetail item(String id,
        {String date = '2026-10-01T00:00:00Z', String type = 'article'}) =>
    ItemDetail.fromJson({
      'id': id,
      'url': 'https://example.test/$id',
      'title': 'needle',
      'summary': 'needle brief',
      'status': 'ready',
      'created_at': date,
      'content_type': type
    });
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });
  late LocalDb db;
  late ApiClient api;
  late SyncService sync;
  final requests = <RequestOptions>[];
  int status = 200;
  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    db = await LocalDb.openAt(inMemoryDatabasePath);
    requests.clear();
    status = 200;
    api = ApiClient(
        dio: Dio()
          ..interceptors.add(InterceptorsWrapper(onRequest: (r, h) {
            requests.add(r);
            h.resolve(Response(
                requestOptions: r,
                statusCode: status,
                data: r.method == 'DELETE' || r.path.endsWith('/restore')
                    ? null
                    : {
                        'id': 'remote',
                        'url': 'https://example.test/remote',
                        'status': 'ready',
                        'title': 'Edited',
                        'summary': 'New brief'
                      }));
          })));
    sync = SyncService(
        pending: db.pendingQueue,
        send: api.syncBatch,
        apply: db.applyMapped,
        markFailed: db.markQueueFailed,
        isOnline: () async => false);
  });
  tearDown(() async {
    await sync.stop();
    api.close();
    await db.close();
  });
  test('queue deletion and Undo preserve retry state', () async {
    final client = await db.queueSave(url: 'https://example.test/queued');
    await db.markQueueFailed(client);
    final before = await db.db.query('sync_queue');
    final a = MemoryActions(
        db: db, api: api, sync: sync, isOnline: () async => false);
    final d = await a.delete('local-$client');
    expect(await db.pendingQueue(), isEmpty);
    expect(await db.localItem('local-$client'), isNull);
    expect(d.localOnly, false);
    await d.undo();
    expect(await db.db.query('sync_queue'), before);
    expect(requests, isEmpty);
  });
  test('hidden cache suppression and Collections preserved', () async {
    await db.upsertRemoteItems([item('remote')]);
    await db.db.insert('collections', {
      'id': 'c',
      'name': 'Keep',
      'urls': '["https://example.test/remote"]',
      'created_at': '2026-10-01'
    });
    final before = await db.db.query('collections');
    final a =
        MemoryActions(db: db, api: api, sync: sync, isOnline: () async => true);
    final d = await a.delete('remote');
    await db.upsertRemoteItems([
      item('remote'),
      ItemDetail.fromJson({'id': 'alias', 'url': 'https://example.test/remote'})
    ]);
    expect(await db.recentLocalItems(), isEmpty);
    await d.undo();
    expect(await db.localItem('remote'), isNotNull);
    expect(await db.db.query('collections'), before);
  });
  test('delete rejection rolls back and failed Undo can retry', () async {
    await db.upsertRemoteItems([item('remote')]);
    final a =
        MemoryActions(db: db, api: api, sync: sync, isOnline: () async => true);
    status = 403;
    await expectLater(a.delete('remote'), throwsA(isA<ApiException>()));
    expect(await db.localItem('remote'), isNotNull);
    status = 200;
    final d = await a.delete('remote');
    status = 503;
    await expectLater(d.undo(), throwsA(isA<ApiException>()));
    expect(await db.localItem('remote'), isNull);
    status = 200;
    await d.undo();
    expect(await db.localItem('remote'), isNotNull);
  });
  test('guest edit local; queued edit rejected; server edit cached', () async {
    await db.upsertRemoteItems([item('remote')]);
    final a = MemoryActions(
        db: db, api: api, sync: sync, guestLibrary: GuestLibrary(db, api));
    final e = await a.edit('remote', title: 'Mine', summary: 'My brief');
    expect(e.bestTitle, 'Mine');
    expect(e.briefText, 'My brief');
    expect(e.edited, true);
    expect(requests, isEmpty);
    final client = await db.queueSave(
        url: 'https://example.test/queued', titleHint: 'Before');
    await expectLater(a.edit('local-$client', title: 'After', summary: 'After'),
        throwsA(isA<ApiException>()));
    expect((await db.localItem('local-$client'))!.bestTitle, 'Before');
    final server = MemoryActions(db: db, api: api, sync: sync);
    await server.edit('remote', title: 'Edited', summary: 'New brief');
    expect((await db.localItem('remote'))!.briefText, 'New brief');
    await server.summarizeAgain('remote', replaceEdits: true);
    expect(requests.last.data, {'replace_edits': true});
  });
  test('inclusive offset cutoff before limit with intelligence', () async {
    await db.upsertRemoteItems([
      item('new', date: '2026-10-02T00:00:00Z', type: 'video'),
      item('boundary'),
      item('old', date: '2026-09-30T23:59:59Z')
    ]);
    final f = {'saved_after': '2026-10-01T02:00:00+02:00', 'type': 'article'};
    expect((await db.recentLocalItems(limit: 1, filters: f)).single.id,
        'boundary');
    expect((await db.localSearch('needle', limit: 1, filters: f)).single.id,
        'boundary');
  });
  test('delete waits upload mapping; Undo cannot revive pending upload',
      () async {
    final client = await db.queueSave(url: 'https://example.test/remote');
    final sending = Completer<void>();
    final release = Completer<void>();
    final racing = SyncService(
        pending: db.pendingQueue,
        send: (_) async {
          sending.complete();
          await release.future;
          return SyncBatchResult(
              mapped: [MappedSave(clientId: client, serverId: 'remote')],
              failedClientIds: []);
        },
        apply: db.applyMapped,
        markFailed: db.markQueueFailed,
        isOnline: () async => true);
    final flush = racing.flush();
    await sending.future;
    final a = MemoryActions(
        db: db, api: api, sync: racing, isOnline: () async => true);
    final deleting = a.delete('local-$client');
    expect(await racing.flush(force: true), 0);
    release.complete();
    await flush;
    final d = await deleting;
    expect(d.id, 'remote');
    await d.undo();
    expect(await db.pendingQueue(), isEmpty);
    await racing.stop();
  });
  test('guest summarize protects edited brief before any request', () async {
    await db.upsertRemoteItems([item('remote')]);
    final a = MemoryActions(
        db: db, api: api, sync: sync, guestLibrary: GuestLibrary(db, api));
    await a.edit('remote', title: 'Mine', summary: 'My brief');
    await expectLater(a.summarizeAgain('remote'), throwsA(isA<ApiException>()));
    expect((await db.localItem('remote'))!.briefText, 'My brief');
    expect(requests, isEmpty);
  });
  test('deleted queue cannot resurrect after database restart', () async {
    final dir = await Directory.systemTemp.createTemp('findback-delete-');
    final path = '${dir.path}/test.db';
    final disk = await LocalDb.openAt(path);
    final client = await disk.queueSave(url: 'https://example.test/persist');
    await disk.hideMemory('local-$client');
    await disk.close();
    final reopened = await LocalDb.openAt(path);
    expect(await reopened.pendingQueue(), isEmpty);
    expect(await reopened.recentLocalItems(), isEmpty);
    await reopened.close();
    await dir.delete(recursive: true);
  });
  test('offline server deletion warns and Undo stays local', () async {
    await db.upsertRemoteItems([item('remote')]);
    final actions = MemoryActions(
        db: db, api: api, sync: sync, isOnline: () async => false);
    final deletion = await actions.delete('remote');
    expect(deletion.localOnly, true);
    await deletion.undo();
    expect(await db.localItem('remote'), isNotNull);
    expect(requests, isEmpty);
  });
  test('guest reprocessing ingests actual URL and schedules only once',
      () async {
    final paths = <String>[];
    final guestApi = ApiClient(
        dio: Dio()
          ..interceptors.add(InterceptorsWrapper(onRequest: (r, h) {
            paths.add(r.path);
            expect(r.data['url'], 'https://example.test/remote');
            h.resolve(Response(requestOptions: r, statusCode: 200, data: {
              'id': 'fresh',
              'status': 'pending',
              'canonical_url': 'https://example.test/remote'
            }));
          })));
    await db.upsertRemoteItems([item('remote')]);
    final actions = MemoryActions(
        db: db,
        api: guestApi,
        sync: sync,
        guestLibrary: GuestLibrary(db, guestApi));
    final fresh = await actions.summarizeAgain('remote');
    expect(fresh.id, 'fresh');
    expect(fresh.isGeneratingBrief, true);
    expect(fresh.briefText, 'needle brief');
    expect(paths, hasLength(1));
    expect(await db.localItem('remote'), isNull);
    guestApi.close();
  });
}
