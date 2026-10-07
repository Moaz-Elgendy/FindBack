import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import 'items_service.dart';

/// Guest Briefs live in SQLite after temporary server processing completes.
class GuestLibrary {
  GuestLibrary(this.db, this.api);
  final LocalDb db;
  final ApiClient api;
  Future<void>? _refreshing;

  Future<void> refresh() async {
    if (_refreshing != null) return _refreshing!;
    _refreshing = _fetch();
    try { await _refreshing; } finally { _refreshing = null; }
  }

  Future<void> cacheAndRelease(ItemDetail item) async {
    await db.upsertRemoteItems([item]);
    if (item.hasFinalBrief) {
      try {
        await api.deleteItem(item.id);
      } on ApiException catch (error) {
        if (error.statusCode != 404 && !error.isRetryableOffline) rethrow;
      }
    }
  }

  Future<ItemDetail> restartExpired(ItemDetail old) async {
    final result = await api.ingestUrl(old.url, titleHint: old.bestTitle);
    final row = old.toLocalRow()..['id'] = result.id..['status'] = result.status;
    await db.db.transaction((transaction) async {
      await transaction.delete('items', where: 'id = ?', whereArgs: [old.id]);
      final existing = await transaction.query('items', where: 'id = ?', whereArgs: [result.id]);
      if (existing.isEmpty) await transaction.insert('items', row);
    });
    return (await db.localItem(result.id))!;
  }

  Future<ItemDetail> read(String id) async {
    final cached = await db.localItem(id);
    if (cached != null && !cached.isGeneratingBrief) return cached;
    try {
      final item = await api.getItem(id);
      await cacheAndRelease(item);
      return item;
    } on ApiException catch (error) {
      if (error.statusCode != 404 || cached == null) rethrow;
      return restartExpired(cached);
    }
  }

  Future<void> _fetch() async {
      final serverIds = <String>{};
      String? next;
      do {
        final page = await api.listItems(limit: 100, cursor: next);
        for (final item in page.items) {
          serverIds.add(item.id);
          await cacheAndRelease(item);
        }
        next = page.nextCursor;
      } while (next != null);
      // A guest lease can expire or the server can discard abandoned staging.
      // Retry URLs locally retained on this device, never another guest's IDs.
      for (final row in await db.db.query('items')) {
        final item = ItemDetail.fromLocalRow(row);
        if (!item.id.startsWith('local-') && item.isGeneratingBrief && !serverIds.contains(item.id)) {
          await restartExpired(item);
        }
      }
  }

  ItemsService get items => ItemsService(
    remoteItem: read, localItem: db.localItem,
    isOnline: () async => true, // Guest reads use SQLite; no connectivity gate.
    remoteRecent: (limit, {category, cursor, filters}) async {
      final rows = await db.recentLocalItems(limit: limit + 1, category: category, filters: filters);
      final items = <ItemDetail>[];
      for (final row in rows.take(limit)) {
        final item = await db.localItem(row.id);
        if (item != null) items.add(item);
      }
      return ItemPage(items: items, nextCursor: rows.length > limit ? 'local:$limit' : null);
    },
    localRecent: (limit, {category, filters}) => db.recentLocalItems(limit: limit, category: category, filters: filters),
    cache: db.upsertRemoteItems,
    remoteDelete: api.deleteItem, localDelete: db.deleteItem,
    dropQueued: db.dropQueued, resolveLocalId: db.syncedItemId,
  );
}
