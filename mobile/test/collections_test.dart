import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/memory_collection.dart';
import 'package:findback/services/collections_service.dart';

ItemDetail memory(String id, String url, {List<String> tools = const ['Claude Code']}) => ItemDetail.fromJson({
  'id': id, 'url': url, 'canonical_url': url, 'status': 'ready',
  'brief_source': 'llm', 'instant_brief': 'A useful brief.', 'topics': ['Artificial Intelligence'],
  'entities': {'tools_products': tools, 'people_orgs': ['Moaz']},
});

class CollectionApi extends ApiClient {
  List<MemoryCollection> remote = [];
  bool offline = false;
  Set<String> removedUrls = {};
  Future<void> Function()? duringSave;
  @override
  Future<List<MemoryCollection>> listCollections() async => remote;
  @override
  Future<MemoryCollection> saveCollection(MemoryCollection value) async {
    if (offline) throw ApiException('offline');
    if (value.urls.any(removedUrls.contains)) throw ApiException('memory removed', statusCode: 404, kind: ApiFailureKind.rejected);
    await duringSave?.call();
    remote = [...remote.where((c) => c.id != value.id), value];
    return value;
  }
  @override
  Future<void> deleteCollection(String id) async {
    if (offline) throw ApiException('offline');
    remote.removeWhere((c) => c.id == id);
  }
}

void main() {
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi; });
  test('guest collections persist and deleting collection preserves memories', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = CollectionApi();
    final service = CollectionsService(db, api, guest: true);
    final item = memory('one', 'https://example.com/one');
    await db.upsertRemoteItems([item]);
    final c = await service.save(name: 'Claude', urls: [item.url, item.url]);
    final restored = CollectionsService(db, api, guest: true);
    expect((await restored.list()).single.urls, [item.url]);
    await restored.remove(c.id);
    expect(await restored.list(), isEmpty);
    expect(await db.localItem(item.id), isNotNull);
    expect(api.remote, isEmpty);
    api.close(); await db.close();
  });
  test('automatic groups use persisted processing metadata and ignore removed saves', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = CollectionApi();
    try {
      await db.upsertRemoteItems([memory('a', 'https://example.com/a'), memory('b', 'https://example.com/b')]);
      final service = CollectionsService(db, api, guest: true);
      expect(automaticCollections(await service.memories()).any((group) => group.name == 'Claude' && group.urls.length == 2), isTrue);
      await db.deleteItem('b');
      expect(automaticCollections(await service.memories()), isEmpty);
    } finally {
      api.close();
      await db.close();
    }
  });

  test('suggestions require two distinct memories and never use people names', () {
    final list = [memory('a', 'https://example.com/a'), memory('b', 'https://example.com/b'), memory('dup', 'https://example.com/a')];
    final suggestions = automaticCollections(list);
    expect(suggestions.any((c) => c.name == 'Claude'), isTrue);
    expect(suggestions.any((c) => c.name == 'Moaz'), isFalse);
    expect(suggestions.first.urls.toSet().length, 2);
    expect(automaticCollections([list.first]), isEmpty);
    for (final state in [{'status': 'failed'}, {'link_only': true}]) {
      final unavailable = ItemDetail.fromJson({'id': 'unavailable', 'url': 'https://example.com/unavailable', 'status': 'ready', 'instant_brief': 'Useful brief.', 'brief_source': 'llm', 'topics': ['Claude'], ...state});
      expect(automaticCollections([list.first, unavailable]), isEmpty);
    }
  });
  test('account edits survive offline and concurrent acknowledgement', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = CollectionApi()..offline = true;
    final service = CollectionsService(db, api, guest: false);
    final c = await service.save(name: 'First', urls: []);
    await service.refresh();
    expect((await service.list()).single.name, 'First');
    api.offline = false;
    api.duringSave = () async { await service.save(id: c.id, name: 'Second', urls: []); api.duringSave = null; };
    await service.refresh();
    expect((await service.list()).single.name, 'Second');
    await service.refresh();
    expect(api.remote.single.name, 'Second');
    await service.remove(c.id); await service.refresh();
    expect(api.remote, isEmpty);
    api.close(); await db.close();
  });
  test('deleting a memory lets a dirty collection synchronize its remaining edits', () async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = CollectionApi();
    final service = CollectionsService(db, api, guest: false);
    final item = memory('one', 'https://example.com/one');
    await db.upsertRemoteItems([item]);
    final queued = await db.queueSave(url: 'https://example.com/pending');
    final collection = await service.save(name: 'First', urls: [item.canonicalUrl]);
    await service.refresh();
    api.offline = true;
    await service.save(id: collection.id, name: 'Renamed', urls: [item.canonicalUrl, 'https://example.com/pending']);
    await db.deleteItem(item.id);
    final local = (await service.list()).single;
    expect(local.urls, ['https://example.com/pending']);
    expect(await db.localItem('local-$queued'), isNotNull);
    api.offline = false;
    api.removedUrls.add(item.canonicalUrl);
    await service.refresh();
    expect(api.remote.single.name, 'Renamed');
    expect(api.remote.single.urls, ['https://example.com/pending']);
    await db.close(); api.close();
  });
  test('SQLite version 4 upgrade preserves memories and queued saves', () async {
    final directory = await Directory.systemTemp.createTemp('collections-upgrade');
    final path = '${directory.path}/legacy.db';
    final legacy = await openDatabase(path, version: 4, onCreate: (db, version) async {
      await db.execute('CREATE TABLE items (id TEXT PRIMARY KEY, url TEXT NOT NULL, summary TEXT)');
      await db.execute('CREATE TABLE sync_queue (client_id TEXT PRIMARY KEY, url TEXT NOT NULL, captured_at TEXT NOT NULL, status TEXT, retries INTEGER, server_id TEXT)');
      await db.insert('items', {'id': 'old', 'url': 'https://example.com/old', 'summary': 'Keep this brief'});
      await db.insert('sync_queue', {'client_id': 'queued', 'url': 'https://example.com/new', 'captured_at': '2026-10-08', 'status': 'pending'});
    });
    await legacy.close();
    final upgraded = await LocalDb.openAt(path);
    expect((await upgraded.db.query('items')).single['summary'], 'Keep this brief');
    expect((await upgraded.db.query('sync_queue')).single['client_id'], 'queued');
    expect(await upgraded.db.query('collections'), isEmpty);
    expect(await upgraded.db.getVersion(), LocalDb.schemaVersion);
    await upgraded.close(); await directory.delete(recursive: true);
  });
  test('guest import copies memberships without exposing another cache', () async {
    final directory = await Directory.systemTemp.createTemp('collections-import');
    final guest = await LocalDb.openAt('${directory.path}/guest.db');
    final account = await LocalDb.openAt('${directory.path}/account.db');
    final other = await LocalDb.openAt('${directory.path}/other.db');
    final api = CollectionApi();
    final item = memory('g', 'https://example.com/g');
    await guest.upsertRemoteItems([item]);
    await CollectionsService(guest, api, guest: true).save(name: 'Mine', urls: [item.url]);
    await account.importGuest(guest);
    await account.importGuest(guest);
    final imported = (await CollectionsService(account, api, guest: false).list()).single;
    expect(imported.id, isNot((await CollectionsService(guest, api, guest: true).list()).single.id));
    expect((await CollectionsService(account, api, guest: false).list()).single.urls, [item.url]);
    expect(await CollectionsService(other, api, guest: false).list(), isEmpty);
    await guest.close(); await account.close(); await other.close(); api.close();
    await directory.delete(recursive: true);
  });
}
