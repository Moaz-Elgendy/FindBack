import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

import '../models/item.dart';
import '../models/json_utils.dart';
import '../models/search_result.dart';
import '../utils/share_text.dart';

/// SQLite mirror of the user's library plus the offline write queue.
///
/// Schema is carried over verbatim from the React Native client so an upgraded
/// install keeps its cached memories. `openAt` is the seam used by tests.
class LocalDb {
  LocalDb(this.db);

  static const String fileName = 'findback.db';
  static const int schemaVersion = 1;
  static const int queueBatchSize = 20;

  /// Bounces allowed before a queued capture is parked as `failed`.
  static const int maxQueueRetries = 3;

  final Database db;

  static Future<LocalDb> openAt(String path) async => LocalDb(
        await openDatabase(
          path,
          version: schemaVersion,
          // onConfigure is the only hook SQLite accepts a journal-mode change
          // in: onCreate runs inside a transaction, where `PRAGMA journal_mode`
          // fails with "cannot change into wal mode from within a transaction".
          onConfigure: (Database database) =>
              database.execute('PRAGMA journal_mode = WAL'),
          onCreate: (Database database, int version) async => _onCreate(database),
        ),
      );

  /// Opens the default app-scoped database. Native-only: needs path_provider.
  static Future<LocalDb> open() async =>
      openAt(p.join(await getDatabasesPath(), fileName));

  static Future<void> _onCreate(Database db) async {
    await db.execute('''
      CREATE TABLE items (
        id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        canonical_url TEXT,
        title TEXT,
        title_clean TEXT,
        summary TEXT,
        category TEXT,
        tags TEXT,
        source_domain TEXT,
        thumbnail_url TEXT,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        match_reason TEXT
      )
    ''');
    await db.execute('''
      CREATE TABLE sync_queue (
        client_id TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        preview TEXT,
        title_hint TEXT,
        captured_at TEXT NOT NULL,
        retries INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending'
      )
    ''');
    await db.execute('CREATE INDEX idx_items_created ON items(created_at DESC)');
    await db.execute('CREATE INDEX idx_queue_status ON sync_queue(status)');
  }

  Future<void> close() => db.close();

  /// Enqueues a save and inserts an optimistic row so search works offline.
  /// Returns the `client_id` the server will later echo back.
  ///
  /// Saving the same URL twice while offline must not enqueue it twice. Both
  /// would map to one server id on sync, and the second rename would collide on
  /// the `items` primary key -- so the user would see a duplicate, or the drain
  /// would throw. The first capture already represents the save, so the repeat
  /// reuses its queue row.
  Future<String> queueSave({
    required String url,
    String? preview,
    String? titleHint,
    DateTime? capturedAt,
  }) async {
    final now = capturedAt ?? DateTime.now();
    final existing = await _pendingForUrl(url);
    if (existing != null) return existing;

    final clientId = '${now.millisecondsSinceEpoch}'
        '-${now.microsecondsSinceEpoch.remainder(1000000).toRadixString(36)}';
    await db.transaction((txn) async {
      await txn.insert('sync_queue', <String, Object?>{
        'client_id': clientId,
        'url': url,
        'preview': preview,
        'title_hint': titleHint,
        'captured_at': now.toUtc().toIso8601String(),
        'status': 'pending',
      });
      await txn.insert('items', <String, Object?>{
        'id': 'local-$clientId',
        'url': url,
        'canonical_url': url,
        'title': deriveLocalTitle(titleHint: titleHint, preview: preview, fallback: url),
        'summary': preview == null ? '' : previewFromText(preview, maxLength: 200),
        'category': 'other',
        'tags': encodeTags(const <String>[]),
        'source_domain': sourceDomain(url),
        'status': 'pending',
        'created_at': now.toUtc().toIso8601String(),
      }, conflictAlgorithm: ConflictAlgorithm.replace);
    });
    return clientId;
  }

  /// The client id of a still-queued capture of this exact URL, if any.
  ///
  /// `failed` rows are included on purpose: the user still has not got this
  /// saved, so a retry of the same link should revive the existing row rather
  /// than add a second one.
  Future<String?> _pendingForUrl(String url) async {
    final rows = await db.query(
      'sync_queue',
      columns: const ['client_id'],
      where: "url = ? AND status IN ('pending', 'failed')",
      whereArgs: <Object?>[url],
      orderBy: 'captured_at ASC',
      limit: 1,
    );
    return rows.isEmpty ? null : rows.first['client_id'] as String;
  }

  Future<List<SyncItem>> pendingQueue({int limit = queueBatchSize}) async {
    final rows = await db.query(
      'sync_queue',
      columns: const ['client_id', 'url', 'preview', 'title_hint', 'captured_at'],
      where: "status = 'pending'",
      orderBy: 'captured_at ASC',
      limit: limit,
    );
    return rows.map(SyncItem.fromRow).toList(growable: false);
  }


  /// Marks queue rows done and adopts the server ids, so the optimistic
  /// `local-<client_id>` row becomes the real row.
  ///
  /// The React Native client only flipped queue status, which left a permanent
  /// ghost row per save: the local copy stayed in `items` forever (showing up in
  /// offline Recent) and delete targeted an id the server never knew about.
  Future<void> applyMapped(List<MappedSave> mapped) async {
    if (mapped.isEmpty) return;
    await db.transaction((txn) async {
      for (final MappedSave save in mapped) {
        await txn.update(
          'sync_queue',
          <String, Object?>{'status': 'done'},
          where: 'client_id = ?',
          whereArgs: <Object?>[save.clientId],
        );
        final localId = 'local-${save.clientId}';
        final serverRowExists = await txn.query(
          'items',
          columns: const ['id'],
          where: 'id = ?',
          whereArgs: <Object?>[save.serverId],
          limit: 1,
        );
        if (serverRowExists.isNotEmpty) {
          // A later fetch already cached the server row; drop the optimistic one.
          await txn.delete('items', where: 'id = ?', whereArgs: <Object?>[localId]);
        } else {
          // Another queued capture of the same URL can map to this same server
          // id. Renaming into it would violate the primary key, so any earlier
          // optimistic row already sitting on that id is dropped first.
          await txn.delete(
            'items',
            where: 'id = ?',
            whereArgs: <Object?>[save.serverId],
          );
          await txn.rawUpdate(
            'UPDATE items SET id = ? WHERE id = ?',
            <Object?>[save.serverId, localId],
          );
        }
      }
    });
  }

  /// A rejected row stays pending and gains a retry counter. After
  /// [maxQueueRetries] bounces it is parked as `failed` so it stops riding the
  /// heartbeat, while its optimistic `items` copy stays behind for the user to
  /// see and delete.
  ///
  /// SQLite evaluates every `SET` expression against the pre-update row, so the
  /// `retries + 1` in the `CASE` is the value this statement is about to write.
  Future<void> markQueueFailed(String clientId) => db.rawUpdate(
        'UPDATE sync_queue SET retries = retries + 1,'
        " status = CASE WHEN retries + 1 >= ? THEN 'failed' ELSE status END"
        ' WHERE client_id = ?',
        <Object?>[maxQueueRetries, clientId],
      );

  /// Substring search over the cached mirror — the offline answer set.
  Future<List<SearchResult>> localSearch(String query, {int limit = 20}) async {
    final like = '%$query%';
    final rows = await db.query(
      'items',
      where: 'title LIKE ? OR title_clean LIKE ? OR summary LIKE ? OR tags LIKE ?',
      whereArgs: <Object?>[like, like, like, like],
      orderBy: 'created_at DESC',
      limit: limit,
    );
    return rows.map(SearchResult.fromLocalRow).toList(growable: false);
  }

  Future<void> upsertRemoteItems(List<ItemDetail> items) async {
    if (items.isEmpty) return;
    await db.transaction((txn) async {
      for (final ItemDetail item in items) {
        await txn.insert('items', item.toLocalRow(), conflictAlgorithm: ConflictAlgorithm.replace);
      }
    });
  }

  Future<List<SearchResult>> recentLocalItems({int limit = 10}) async {
    final rows = await db.query('items', orderBy: 'created_at DESC', limit: limit);
    return rows.map(SearchResult.fromLocalRow).toList(growable: false);
  }

  /// The cached copy of one item, or null when it was never seen online.
  Future<ItemDetail?> localItem(String id) async {
    final rows = await db.query('items', where: 'id = ?', whereArgs: <Object?>[id], limit: 1);
    if (rows.isEmpty) return null;
    return ItemDetail.fromLocalRow(rows.first);
  }

  Future<int> deleteItem(String id) => db.delete('items', where: 'id = ?', whereArgs: <Object?>[id]);

  /// Drops a queued capture the user deleted before it ever uploaded, so a
  /// cancelled save cannot resurrect itself on the next flush.
  Future<int> dropQueued(String clientId) =>
      db.delete('sync_queue', where: 'client_id = ?', whereArgs: <Object?>[clientId]);

  Future<int> pendingCount() async {
    final row = await db.rawQuery("SELECT COUNT(*) AS c FROM sync_queue WHERE status = 'pending'");
    return row.first['c'] as int? ?? 0;
  }
}

