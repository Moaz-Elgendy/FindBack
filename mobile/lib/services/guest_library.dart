import 'dart:convert';
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
    if (db.isHidden(item)) return;
    final cachedRows = await db.db.query('items', where: 'id = ?', whereArgs: [item.id]);
    final cached = cachedRows.isEmpty ? null : ItemDetail.fromLocalRow(cachedRows.single);
    final payload = cachedRows.isEmpty ? <String, dynamic>{} :
        jsonDecode(cachedRows.single['brief_payload'] as String) as Map<String, dynamic>;
    final previous = payload['guest_reprocess_previous'];
    var finishedReprocess = false;
    if (previous is Map) {
      if (item.isGeneratingBrief && !item.isFailed) {
        final row = Map<String, Object?>.from(cachedRows.single)..['status'] = item.status;
        await db.db.update('items', row, where: 'id = ?', whereArgs: [item.id]);
        return;
      }
      finishedReprocess = true;
      if (item.isFailed || item.needsRetry || !item.hasFinalBrief) {
        final row = Map<String, Object?>.from(previous)..['id'] = item.id;
        final oldPayload = jsonDecode(row['brief_payload'] as String) as Map<String, dynamic>;
        oldPayload['reprocessing'] = false;
        oldPayload['reprocess_failure'] = 'This page could not be read. Your previous brief was kept.';
        row['brief_payload'] = jsonEncode(oldPayload);
        await db.upsertRemoteItems([ItemDetail.fromLocalRow(row)]);
      } else {
        await db.upsertRemoteItems([item]);
      }
    } else if (cached?.edited != true && cached?.reprocessFailure == null) {
      await db.upsertRemoteItems([item]);
    }
    if (finishedReprocess || item.hasFinalBrief || (cached?.reprocessFailure != null && item.isFailed)) {
      try {
        await api.deleteItem(item.id);
      } on ApiException catch (error) {
        if (error.statusCode != 404 && !error.isRetryableOffline) rethrow;
      }
    }
  }

  Future<ItemDetail> restartExpired(ItemDetail old, {bool reprocess = false}) async {
    if (db.isHidden(old)) throw StateError('Memory was deleted');
    final result = await api.ingestUrl(old.url, titleHint: old.bestTitle);
    final cachedRows = await db.db.query('items', where: 'id = ?', whereArgs: [old.id]);
    final row = (cachedRows.isEmpty ? old.toLocalRow() : Map<String, Object?>.from(cachedRows.single));
    if (reprocess) {
      final payload = jsonDecode(row['brief_payload'] as String) as Map<String, dynamic>;
      payload['guest_reprocess_previous'] = Map<String, Object?>.from(row);
      payload['reprocessing'] = true;
      payload['reprocess_failure'] = null;
      row['brief_payload'] = jsonEncode(payload);
    }
    row['id'] = result.id;
    row['status'] = result.status;
    await db.db.transaction((transaction) async {
      if (db.isHidden(old)) return;
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
