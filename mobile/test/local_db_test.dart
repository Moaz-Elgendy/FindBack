import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

ItemDetail _serverItem(String id, {String title = 'Server recipe'}) =>
    ItemDetail.fromJson(<String, Object?>{
      'id': id,
      'url': 'https://example.com/$id',
      'title': title,
      'summary': 'A cached summary about pasta',
      'category': 'recipe',
      'tags': <String>['dinner'],
      'status': 'ready',
      'created_at': '2026-09-29T10:00:00.000Z',
    });

Future<LocalDb> _freshDb() => LocalDb.openAt(inMemoryDatabasePath);

void main() {
  late LocalDb db;

  setUpAll(() {
    // The mirror is the offline contract; run it against real SQLite rather
    // than a mock so schema mistakes fail here and not on a phone.
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  setUp(() async => db = await _freshDb());
  tearDown(() async => db.close());

  test('a queued save is searchable immediately, before any upload', () async {
    final String clientId = await db.queueSave(
      url: 'https://example.com/fennel',
      preview: 'Fennel pasta with lemon',
      titleHint: 'Fennel pasta',
    );

    expect(await db.pendingCount(), 1);
    final List<SyncItem> queued = await db.pendingQueue();
    expect(queued.single.clientId, clientId);
    expect(queued.single.titleHint, 'Fennel pasta');

    final SearchResult row = (await db.recentLocalItems()).single;
    expect(row.id, 'local-$clientId');
    expect(row.isLocalOnly, isTrue);
    expect(row.title, 'Fennel pasta');
    expect(row.sourceDomain, 'example.com');
    expect((await db.localSearch('fennel')).single.id, 'local-$clientId');
  });

  test('the server id replaces the optimistic one once a save lands', () async {
    final String clientId = await db.queueSave(url: 'https://example.com/x');
    await db.applyMapped(<MappedSave>[MappedSave(clientId: clientId, serverId: 'uuid-real')]);

    expect(await db.pendingCount(), 0);
    expect(await db.localItem('local-$clientId'), isNull);
    expect((await db.localItem('uuid-real'))!.id, 'uuid-real');
  });

  test('a cached server row wins over the optimistic copy', () async {
    final String clientId = await db.queueSave(url: 'https://example.com/x');
    await db.upsertRemoteItems(<ItemDetail>[_serverItem('uuid-real', title: 'Enriched')]);
    await db.applyMapped(<MappedSave>[MappedSave(clientId: clientId, serverId: 'uuid-real')]);

    final List<SearchResult> rows = await db.recentLocalItems();
    expect(rows.length, 1);
    expect(rows.single.title, 'Enriched');
    expect(rows.single.isLocalOnly, isFalse);
  });

  test('a save the server keeps refusing is parked, not retried forever', () async {
    final String clientId = await db.queueSave(url: 'https://example.com/bad');

    for (int attempt = 0; attempt < LocalDb.maxQueueRetries; attempt++) {
      expect(await db.pendingQueue(), isNotEmpty, reason: 'attempt ${attempt + 1} should retry');
      await db.markQueueFailed(clientId);
    }

    expect(await db.pendingQueue(), isEmpty);
    expect(await db.pendingCount(), 0);
    // The user still sees the save and can delete it.
    expect((await db.localItem('local-$clientId'))!.url, 'https://example.com/bad');
    expect(await db.deleteItem('local-$clientId'), 1);
  });

  test('cancelling a save before it uploads stops the resurrection', () async {
    final String clientId = await db.queueSave(url: 'https://example.com/gone');
    await db.deleteItem('local-$clientId');
    expect(await db.dropQueued(clientId), 1);
    expect(await db.pendingQueue(), isEmpty);
    expect(await db.pendingCount(), 0);
  });

  test('the mirror round-trips a fetched item', () async {
    await db.upsertRemoteItems(<ItemDetail>[_serverItem('uuid-1')]);
    await db.upsertRemoteItems(<ItemDetail>[_serverItem('uuid-1', title: 'Renamed')]);

    final ItemDetail cached = (await db.localItem('uuid-1'))!;
    expect(cached.bestTitle, 'Renamed');
    expect(cached.tags, <String>['dinner']);
    expect(cached.isReady, isTrue);
    expect((await db.localSearch('pasta')).single.id, 'uuid-1');
    expect(await db.localSearch('nothing here'), isEmpty);
    expect(await db.deleteItem('uuid-1'), 1);
    expect(await db.recentLocalItems(), isEmpty);
  });

  // --- Phase 15: the offline contract --------------------------------------

  test('an offline capture is stored, searchable and counted as pending',
      () async {
    // The capture happened with no network. Nothing about that may cost the
    // user their link, and they must be able to see it is not yet saved.
    final String clientId = await db.queueSave(
      url: 'https://example.com/offline-talk',
      preview: 'A talk saved on a train',
      titleHint: 'Offline talk',
    );

    final List<SearchResult> found = await db.localSearch('offline talk');
    expect(found, hasLength(1), reason: 'the capture must be findable offline');
    expect(found.single.id, 'local-$clientId');
    expect(await db.pendingCount(), 1);
    final List<SyncItem> queued = await db.pendingQueue();
    expect(queued.single.url, 'https://example.com/offline-talk');
    expect(queued.single.clientId, clientId);
  });

  test('saving the same link twice offline queues it once', () async {
    // Two captures of one URL both map to the same server id on sync. Enqueuing
    // both would leave the optimistic rows colliding on the items primary key,
    // and the user would see the link twice.
    final String first = await db.queueSave(url: 'https://example.com/same');
    final String second = await db.queueSave(url: 'https://example.com/same');

    expect(second, first, reason: 'the repeat must reuse the queued capture');
    expect(await db.pendingQueue(), hasLength(1));
    expect(await db.recentLocalItems(), hasLength(1));
  });

  test('after sync the capture appears exactly once, under the server id',
      () async {
    final String clientId = await db.queueSave(
      url: 'https://example.com/dedupe',
      titleHint: 'Dedupe me',
    );
    // The same link saved twice offline is still one capture.
    await db.queueSave(url: 'https://example.com/dedupe');

    await db.applyMapped(<MappedSave>[
      MappedSave(clientId: clientId, serverId: 'server-42'),
    ]);

    final List<SearchResult> items = await db.recentLocalItems();
    expect(items, hasLength(1), reason: 'no duplicate after sync');
    expect(items.single.id, 'server-42');
    expect(await db.pendingCount(), 0, reason: 'the queue is empty once synced');
  });

  test('a link that already synced can be saved offline again', () async {
    // The dedupe guard is about *unsynced* captures. Once one has landed, a
    // deliberate re-save is the user saving it again, and must not be swallowed.
    final String clientId = await db.queueSave(url: 'https://example.com/twice');
    await db.applyMapped(<MappedSave>[
      MappedSave(clientId: clientId, serverId: 'server-1'),
    ]);

    final String again = await db.queueSave(url: 'https://example.com/twice');
    expect(again, isNot(clientId));
    expect(await db.pendingQueue(), hasLength(1));
  });

  test('a queued capture the server refused is revived, not duplicated',
      () async {
    // Parked rows count as not-yet-saved: the user still does not have this
    // link on the server, so re-sharing it must not add a second copy.
    final String first = await db.queueSave(url: 'https://example.com/refused');
    for (int i = 0; i < LocalDb.maxQueueRetries; i++) {
      await db.markQueueFailed(first);
    }

    final String again = await db.queueSave(url: 'https://example.com/refused');
    expect(again, first, reason: 'the parked capture is revived');
    expect(await db.recentLocalItems(), hasLength(1));
  });
}
