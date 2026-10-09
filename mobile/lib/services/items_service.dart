import 'package:flutter/foundation.dart';

import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import '../models/search_result.dart';
import 'sync_service.dart' show ConnectivityProbe, systemIsOnline;

typedef RemoteItemFetch = Future<ItemDetail> Function(String id);
typedef LocalItemFetch = Future<ItemDetail?> Function(String id);
typedef RemoteRecentFetch = Future<ItemPage> Function(int limit, {String? category, String? cursor, Map<String, String>? filters});
typedef LocalRecentFetch = Future<List<SearchResult>> Function(int limit, {String? category, Map<String, String>? filters});
typedef ItemCache = Future<void> Function(List<ItemDetail> items);
typedef RemoteDelete = Future<void> Function(String id);
typedef LocalDelete = Future<int> Function(String id);
typedef QueueDrop = Future<int> Function(String clientId);
typedef RemoteOpen = Future<void> Function(String id);
typedef OpenQueuer = Future<void> Function(String itemId, {required String url});

enum DeleteStatus {
  /// Gone from the server and the device.
  deleted,

  /// Removed from this device only; the server copy is still there.
  localOnly,
}

class DeleteOutcome {
  const DeleteOutcome(this.status, {this.detail});

  final DeleteStatus status;
  final String? detail;

  bool get needsNetwork => status == DeleteStatus.localOnly;
}

/// Reads and deletes library items, keeping the SQLite mirror warm.
///
/// Every read prefers the API and falls back to the cache, so the screen still
/// answers when the network is the thing that failed.
class ItemsService {
  ItemsService({
    required RemoteItemFetch remoteItem,
    required LocalItemFetch localItem,
    required RemoteRecentFetch remoteRecent,
    required LocalRecentFetch localRecent,
    required ItemCache cache,
    required RemoteDelete remoteDelete,
    required LocalDelete localDelete,
    required QueueDrop dropQueued,
    ConnectivityProbe? isOnline,
    bool Function(ItemDetail)? isHidden,
    Future<String?> Function(String localId)? resolveLocalId,
    RemoteOpen? markRemoteOpened,
    OpenQueuer? queueOpen,
  })  : _remoteItem = remoteItem,
        _localItem = localItem,
        _isHidden = isHidden,
        _remoteRecent = remoteRecent,
        _localRecent = localRecent,
        _cache = cache,
        _remoteDelete = remoteDelete,
        _localDelete = localDelete,
        _dropQueued = dropQueued,
        _isOnline = isOnline ?? systemIsOnline,
        _resolveLocalId = resolveLocalId,
        _markRemoteOpened = markRemoteOpened,
        _queueOpen = queueOpen;

  factory ItemsService.of({required ApiClient api, required LocalDb db, ConnectivityProbe? isOnline}) =>
      ItemsService(
        remoteItem: api.getItem,
        isHidden: db.isHidden,
        localItem: db.localItem,
        remoteRecent: (int limit, {String? category, String? cursor, Map<String, String>? filters}) =>
            api.listItems(limit: limit, category: category, cursor: cursor, filters: filters),
        localRecent: (int limit, {String? category, Map<String, String>? filters}) =>
            db.recentLocalItems(limit: limit, category: category, filters: filters),
        cache: db.upsertRemoteItems,
        remoteDelete: api.deleteItem,
        localDelete: db.deleteItem,
        dropQueued: db.dropQueued,
        isOnline: isOnline,
        resolveLocalId: db.syncedItemId,
        markRemoteOpened: api.markOpened,
        queueOpen: db.queueOpen,
      );

  static const String localPrefix = 'local-';

  final bool Function(ItemDetail)? _isHidden;

  final RemoteItemFetch _remoteItem;
  final LocalItemFetch _localItem;
  final RemoteRecentFetch _remoteRecent;
  final LocalRecentFetch _localRecent;
  final ItemCache _cache;
  final RemoteDelete _remoteDelete;
  final LocalDelete _localDelete;
  final QueueDrop _dropQueued;
  final ConnectivityProbe _isOnline;
  final Future<String?> Function(String localId)? _resolveLocalId;
  final RemoteOpen? _markRemoteOpened;
  final OpenQueuer? _queueOpen;

  static bool isLocalId(String id) => id.startsWith(localPrefix);

  /// One item: the API copy when reachable (and cached on the way through), the
  /// mirror when not. A `local-*` id was never uploaded, so don't ask the API.
  Future<ItemDetail?> getItem(String id) async {
    if (isLocalId(id)) {
      final local = await _localItem(id);
      if (local != null) return local;
      final serverId = await _resolveLocalId?.call(id);
      return serverId == null ? null : getItem(serverId);
    }
    if (await _isOnline()) {
      try {
        final ItemDetail item = await _remoteItem(id);
        await _cache(<ItemDetail>[item]);
        return _isHidden?.call(item) == true ? null : item;
      } on ApiException catch (error) {
        if (!error.isRetryableOffline) rethrow;
      }
    }
    return _localItem(id);
  }

  /// The library list, always in newest-first order.
  Future<List<SearchResult>> recent({int limit = 10, String? category, String? cursor, Map<String, String>? filters}) async =>
      (await recentPage(limit: limit, category: category, cursor: cursor, filters: filters)).items;

  Future<({List<SearchResult> items, String? nextCursor})> recentPage({
    int limit = 20, String? category, String? cursor, int loadedCount = 0, Map<String, String>? filters,
  }) async {
    final localCursor = cursor?.startsWith('local:') == true;
    if (!localCursor && await _isOnline()) {
      try {
        final ItemPage page = await _remoteRecent(limit, category: category, cursor: cursor, filters: filters);
        await _cache(page.items);
        return (items: page.items.where((item) => _isHidden?.call(item) != true).map(SearchResult.fromItem).toList(growable: false),
            nextCursor: page.nextCursor);
      } on ApiException catch (error) {
        if (!error.isRetryableOffline) rethrow;
      }
    }
    final offset = localCursor ? int.parse(cursor!.substring(6)) : loadedCount;
    final rows = await _localRecent(offset + limit + 1, category: category, filters: filters);
    return (items: rows.skip(offset).take(limit).toList(growable: false),
        nextCursor: rows.length > offset + limit ? 'local:${offset + limit}' : null);
  }

  /// Deletes locally no matter what, so the item disappears from the device the
  /// moment the user confirms — the RN client left it on screen when offline.
  Future<DeleteOutcome> remove(String id) async {
    if (isLocalId(id)) {
      await _localDelete(id);
      await _dropQueued(id.substring(localPrefix.length));
      return const DeleteOutcome(DeleteStatus.deleted);
    }

    if (await _isOnline()) {
      try {
        await _remoteDelete(id);
        await _localDelete(id);
        return const DeleteOutcome(DeleteStatus.deleted);
      } on ApiException catch (error) {
        // 404: already gone server-side, so the local copy is the only cleanup
        // left to do. Anything else is a real failure, but we still clear the
        // device and report it rather than lying about success.
        await _localDelete(id);
        if (error.statusCode == 404) return const DeleteOutcome(DeleteStatus.deleted);
        if (error.isRetryableOffline) {
          return DeleteOutcome(DeleteStatus.localOnly, detail: error.message);
        }
        rethrow;
      }
    }

    await _localDelete(id);
    return const DeleteOutcome(DeleteStatus.localOnly, detail: 'offline');
  }

  /// Records that the user opened this memory.
  ///
  /// The API stamps `first_opened_at` server-side; when that call fails for
  /// any reason (offline first among them) the open waits in the sync queue
  /// instead. Never throws and never blocks: the detail screen fires this
  /// without awaiting it, and a `local-*` row was never uploaded, so there is
  /// nothing to mark.
  Future<void> markOpened(ItemDetail item) async {
    if (isLocalId(item.id)) return;
    try {
      await _markRemoteOpened?.call(item.id);
      return;
    } catch (error) {
      debugPrint('[items] mark-opened failed, queued for retry: $error');
    }
    try {
      final OpenQueuer? queue = _queueOpen;
      if (queue != null) await queue(item.id, url: item.url);
    } catch (error) {
      debugPrint('[items] could not queue the open: $error');
    }
  }
}
