import 'dart:convert';
import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import '../models/memory_collection.dart';

class CollectionsService {
  CollectionsService(this.db, this.api, {required this.guest});
  final LocalDb db;
  final ApiClient api;
  final bool guest;
  Future<void>? _refreshing;

  MemoryCollection _fromRow(Map<String, Object?> row) => MemoryCollection(
    id: row['id'] as String, name: row['name'] as String,
    urls: (jsonDecode(row['urls'] as String) as List).cast<String>(),
  );

  Future<List<MemoryCollection>> list() async => (await db.db.query('collections',
    where: 'deleted = 0', orderBy: 'created_at, id')).map(_fromRow).toList();

  Future<List<ItemDetail>> memories() async =>
      (await db.db.query('items', orderBy: 'created_at DESC')).map(ItemDetail.fromLocalRow).toList();

  Future<MemoryCollection> save({String? id, required String name, required List<String> urls}) async {
    name = name.trim();
    if (name.isEmpty || name.length > 80) throw ArgumentError('Use a name between 1 and 80 characters');
    if (urls.length > 1000) throw ArgumentError('A collection can hold up to 1000 memories');
    final collection = MemoryCollection(id: id ?? newCollectionId(), name: name, urls: urls.toSet().toList());
    await db.db.transaction((txn) async {
      final existing = await txn.query('collections', where: 'id = ?', whereArgs: [collection.id]);
      if (existing.isEmpty) {
        await txn.insert('collections', {'id': collection.id, 'name': name, 'urls': jsonEncode(collection.urls),
          'revision': 1, 'dirty': 1, 'deleted': 0, 'created_at': DateTime.now().toUtc().toIso8601String()});
      } else {
        await txn.rawUpdate('UPDATE collections SET name = ?, urls = ?, revision = revision + 1, dirty = 1, deleted = 0 WHERE id = ?',
          [name, jsonEncode(collection.urls), collection.id]);
      }
    });
    return collection;
  }

  Future<void> remove(String id) async {
    if (guest) {
      await db.db.delete('collections', where: 'id = ?', whereArgs: [id]);
    } else {
      await db.db.rawUpdate('UPDATE collections SET deleted = 1, dirty = 1, revision = revision + 1 WHERE id = ?', [id]);
    }
  }

  Future<void> refresh() async {
    if (guest) return;
    if (_refreshing != null) return _refreshing!;
    _refreshing = _sync();
    try { await _refreshing; } finally { _refreshing = null; }
  }

  Future<void> _sync() async {
    try {
      for (final row in await db.db.query('collections', where: 'dirty = 1')) {
        final id = row['id'] as String;
        try {
          if (row['deleted'] == 1) {
            await api.deleteCollection(id);
            await db.db.delete('collections', where: 'id = ? AND revision = ?', whereArgs: [id, row['revision']]);
          } else {
            await api.saveCollection(_fromRow(row));
            await db.db.update('collections', {'dirty': 0}, where: 'id = ? AND revision = ?', whereArgs: [id, row['revision']]);
          }
        } on ApiException catch (error) {
          // Offline memories may not have reached the server yet.
          if (error.statusCode == 404) continue;
          rethrow;
        }
      }
      final remote = await api.listCollections();
      await db.db.transaction((txn) async {
        final current = await txn.query('collections');
        final byId = {for (final row in current) row['id']: row};
        for (final collection in remote) {
          if (byId[collection.id]?['dirty'] == 1) continue;
          final row = {'name': collection.name, 'urls': jsonEncode(collection.urls), 'dirty': 0, 'deleted': 0};
          if (byId.containsKey(collection.id)) {
            await txn.update('collections', row, where: 'id = ?', whereArgs: [collection.id]);
          } else {
            await txn.insert('collections', {...row, 'id': collection.id, 'revision': 0,
              'created_at': DateTime.now().toUtc().toIso8601String()});
          }
        }
        final ids = remote.map((c) => c.id).toSet();
        for (final row in current) {
          if (row['dirty'] == 0 && !ids.contains(row['id'])) {
            await txn.delete('collections', where: 'id = ?', whereArgs: [row['id']]);
          }
        }
      });
    } on ApiException catch (error) {
      if (!error.isRetryableOffline) rethrow;
    }
  }
}

List<MemoryCollection> collectionSuggestions(List<ItemDetail> items) {
  final groups = <String, Set<String>>{};
  for (final item in items.where((i) => i.hasFinalBrief)) {
    final tools = (item.entities['tools_products'] as List? ?? []).whereType<String>();
    for (final raw in {...item.topics, ...tools}) {
      final label = RegExp(r'^claude(?:\s|$)', caseSensitive: false).hasMatch(raw) ? 'Claude' : raw.trim();
      if (label.isEmpty || label.length > 80) continue;
      groups.putIfAbsent(label, () => {}).add(item.canonicalUrl);
    }
  }
  return [for (final entry in groups.entries) if (entry.value.length >= 2)
    MemoryCollection(id: 'suggestion-${entry.key}', name: entry.key, urls: entry.value.toList())]
    ..sort((a, b) => b.urls.length.compareTo(a.urls.length));
}
