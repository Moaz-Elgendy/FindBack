import 'dart:convert';
import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';

import '../models/item.dart';
import '../models/memory_collection.dart';
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
  static const int schemaVersion = 8;
  static const int queueBatchSize = 20;

  /// Bounces allowed before a queued capture is parked as `failed`.
  static const int maxQueueRetries = 3;

  final Database db;
  final Set<String> _hiddenIds = {};
  final Set<String> _hiddenUrls = {};

  bool isHidden(ItemDetail item) => _hidden(item);
  bool isHiddenId(String id) => _hiddenIds.contains(id);

  bool _hidden(ItemDetail item) => _hiddenIds.contains(item.id) ||
      _hiddenUrls.contains(item.url) || _hiddenUrls.contains(item.canonicalUrl);

  Future<LocalMemorySnapshot> hideMemory(String id) async {
    final snapshot = await db.transaction((txn) async {
      final rows = await txn.query('items', where: 'id = ?', whereArgs: [id]);
      final queues = await txn.query('sync_queue',
          where: 'client_id = ? OR server_id = ?',
          whereArgs: [id.startsWith('local-') ? id.substring(6) : '', id]);
      if (rows.isEmpty) throw StateError('Memory is no longer available');
      final item = ItemDetail.fromLocalRow(rows.single);
      _hiddenIds.add(id);
      _hiddenUrls.addAll([item.url, item.canonicalUrl]);
      await txn.delete('items', where: 'id = ?', whereArgs: [id]);
      for (final row in queues) {
        await txn.delete('sync_queue', where: 'client_id = ?', whereArgs: [row['client_id']]);
      }
      return LocalMemorySnapshot(rows.single, queues);
    });
    return snapshot;
  }

  Future<void> restoreMemory(LocalMemorySnapshot snapshot) async {
    await db.transaction((txn) async {
      await txn.insert('items', snapshot.item, conflictAlgorithm: ConflictAlgorithm.replace);
      for (final row in snapshot.queue) {
        await txn.insert('sync_queue', row, conflictAlgorithm: ConflictAlgorithm.replace);
      }
    });
    final item = ItemDetail.fromLocalRow(snapshot.item);
    _hiddenIds.remove(item.id);
    _hiddenUrls.removeAll([item.url, item.canonicalUrl]);
  }

  bool _matchesFilters(SearchResult row, Map<String, String>? filters) {
    final intelligence = Map<String, String>.from(filters ?? {});
    final cutoff = intelligence.remove('saved_after');
    if (cutoff != null) {
      final savedAfter = DateTime.parse(cutoff);
      if (row.createdAt == null || row.createdAt!.isBefore(savedAfter)) return false;
    }
    return matchesIntelligence(row, intelligence);
  }


  static Future<LocalDb> openAt(String path) async => LocalDb(
        await openDatabase(
          path,
          version: schemaVersion,
          // onConfigure is the only hook SQLite accepts a journal-mode change
          // in: onCreate runs inside a transaction, where `PRAGMA journal_mode`
          // fails with "cannot change into wal mode from within a transaction".
          onConfigure: (Database database) =>
              database.rawQuery('PRAGMA journal_mode = WAL'),
          onCreate: (Database database, int version) async => _onCreate(database),
          onUpgrade: (Database database, int oldVersion, int newVersion) async {
            if (oldVersion < 8) await _createPreferences(database);
            if (oldVersion < 6) await _createReminders(database);
            if (oldVersion < 5) await _createCollections(database);
            if (oldVersion < 2) await database.execute("ALTER TABLE items ADD COLUMN brief_payload TEXT DEFAULT '{}'");
            if (oldVersion < 4) {
              final columns = await database.rawQuery('PRAGMA table_info(sync_queue)');
              if (!columns.any((column) => column['name'] == 'server_id')) {
                await database.execute('ALTER TABLE sync_queue ADD COLUMN server_id TEXT');
              }
            }
            // v7: a queue row can now be a save OR a mark-opened call; every
            // pre-existing row is a save. The presence guard mirrors the v4
            // one: tests reopen a new-shape database with an old version
            // stamp, so the column may already exist.
            if (oldVersion < 7) {
              final queueColumns = await database.rawQuery('PRAGMA table_info(sync_queue)');
              if (!queueColumns.any((column) => column['name'] == 'kind')) {
                await database.execute(
                    "ALTER TABLE sync_queue ADD COLUMN kind TEXT NOT NULL DEFAULT 'save'");
              }
            }
          },
        ),
      );

  /// Opens the default app-scoped database. Native-only: needs path_provider.
  static Future<LocalDb> open({String? scope}) async {
    if (scope != null && !RegExp(r'^[a-zA-Z0-9_-]{1,80}$').hasMatch(scope)) {
      throw ArgumentError('Invalid account cache scope');
    }
    return openAt(p.join(await getDatabasesPath(), scope == null ? fileName : 'findback-$scope.db'));
  }

  /// Copy guest memories without moving or deleting the original database.
  Future<void> importGuest(LocalDb guest) async {
    for (final row in await guest.db.query('items')) {
      final item = ItemDetail.fromLocalRow(row);
      final existing = await localItemForUrl(item.url);
      if (existing != null && !existing.id.startsWith('local-')) {
        await _importGuestReminder(guest, item.id, existing.id);
        continue;
      }
      final client = existing?.id.substring('local-'.length) ?? await queueSave(
          url: item.url, preview: item.briefText, titleHint: item.bestTitle, capturedAt: item.createdAt);
      final cached = Map<String, Object?>.from(row)
        ..['id'] = 'local-$client'
        ..['status'] = item.hasFinalBrief ? 'ready' : item.status;
      await db.insert('items', cached, conflictAlgorithm: ConflictAlgorithm.replace);
      await _importGuestReminder(guest, item.id, 'local-$client');
    }
    for (final row in await guest.db.query('collections', where: 'deleted = 0')) {
      final origin = row['origin_id'] ?? row['id'];
      if ((await db.query('collections', where: 'origin_id = ?', whereArgs: [origin])).isNotEmpty) continue;
      await db.insert('collections', Map<String, Object?>.from(row)
        ..['id'] = newCollectionId()..['origin_id'] = origin..['dirty'] = 1);
    }
  }

  Future<void> _importGuestReminder(LocalDb guest, String from, String to) async {
    for (final row in await guest.db.query('reminders',
        where: 'item_id=? AND delivered=0 AND suspended=0 AND dirty!=?', whereArgs: [from, 'remove'])) {
      await db.insert('reminders', {'item_id': to, 'scheduled_at': row['scheduled_at'],
        'time_zone': row['time_zone'], 'dirty': 'set'}, conflictAlgorithm: ConflictAlgorithm.ignore);
    }
  }

  static Future<void> _createCollections(Database db) => db.execute("""
    CREATE TABLE IF NOT EXISTS collections (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, urls TEXT NOT NULL DEFAULT '[]',
      revision INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 0,
      deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
      origin_id TEXT UNIQUE
    )
  """);

  static Future<void> _createReminders(Database db) => db.execute("""
    CREATE TABLE IF NOT EXISTS reminders (notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
      item_id TEXT NOT NULL UNIQUE, scheduled_at TEXT NOT NULL, time_zone TEXT NOT NULL,
      delivered INTEGER NOT NULL DEFAULT 0, dirty TEXT NOT NULL DEFAULT '',
      suspended INTEGER NOT NULL DEFAULT 0, scheduled INTEGER NOT NULL DEFAULT 0)
  """);

  static Future<void> _createPreferences(Database db) => db.execute(
      'CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)');

  Future<bool> get deleteConfirmationSuppressed async {
    final rows = await db.query('preferences',
        where: 'key = ?', whereArgs: ['skip_delete_confirmation']);
    return rows.isNotEmpty && rows.single['value'] == '1';
  }

  Future<void> setDeleteConfirmationSuppressed(bool suppressed) async {
    await db.insert('preferences',
        {'key': 'skip_delete_confirmation', 'value': suppressed ? '1' : '0'},
        conflictAlgorithm: ConflictAlgorithm.replace);
  }

  static Future<void> _onCreate(Database db) async {
    await _createPreferences(db);
    await _createReminders(db);
    await _createCollections(db);
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
        match_reason TEXT,
        brief_payload TEXT DEFAULT '{}'
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
        status TEXT DEFAULT 'pending',
        server_id TEXT,
        kind TEXT NOT NULL DEFAULT 'save'
      )
    ''');
    await db.execute('CREATE INDEX idx_items_created ON items(created_at DESC)');
    await db.execute('CREATE INDEX idx_queue_status ON sync_queue(status)');
  }

  Future<void> close() => db.close();

  Future<void> clearAccountData() async {
    await db.transaction((txn) async {
      for (final table in ['preferences', 'collections', 'reminders', 'sync_queue', 'items']) {
        await txn.delete(table);
      }
    });
    _hiddenIds.clear();
    _hiddenUrls.clear();
  }

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

    final clientClock = DateTime.now();
    final clientId = '${clientClock.millisecondsSinceEpoch}'
        '-${clientClock.microsecondsSinceEpoch.remainder(1000000).toRadixString(36)}';
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
  ///
  /// Reviving is the whole point of returning a parked row, so it happens here.
  /// Handing back the client id of a row that stays `failed` looks like a
  /// revival and is not one: `pendingQueue` only ever selects `pending`, so the
  /// capture would never be uploaded no matter how often the user re-saved it.
  /// A re-save is the user asking again, so the attempts start over.
  Future<String?> _pendingForUrl(String url) async {
    final rows = await db.query(
      'sync_queue',
      columns: const ['client_id', 'status'],
      where: "url = ? AND kind = 'save' AND status IN ('pending', 'failed')",
      whereArgs: <Object?>[url],
      orderBy: 'captured_at ASC',
      limit: 1,
    );
    if (rows.isEmpty) return null;
    final Map<String, Object?> row = rows.first;
    final String clientId = row['client_id'] as String;
    if (row['status'] == 'failed') {
      await db.update(
        'sync_queue',
        <String, Object?>{'status': 'pending', 'retries': 0},
        where: 'client_id = ?',
        whereArgs: <Object?>[clientId],
      );
    }
    return clientId;
  }

  Future<List<SyncItem>> pendingQueue({int limit = queueBatchSize}) async {
    final rows = await db.query(
      'sync_queue',
      columns: const ['client_id', 'url', 'preview', 'title_hint', 'captured_at'],
      where: "status = 'pending' AND kind = 'save'",
      orderBy: 'captured_at ASC',
      limit: limit,
    );
    final pending = <SyncItem>[];
    for (final row in rows) {
      final cached = await localItem('local-${row['client_id']}');
      final completed = cached != null && (cached.isReady || cached.isFailed || cached.hasFinalBrief || cached.edited || cached.linkOnly);
      if (completed && !cached.isFailed && !cached.isReady) {
        await db.update('items', {'status': 'ready'}, where: 'id = ?', whereArgs: [cached.id]);
      }
      pending.add(SyncItem.fromRow({...row,
        if (completed) 'saved_memory': cached.toSavedMemory(),
      }));
    }
    return pending;
  }

  /// Prefix of a `sync_queue` row that is a pending "memory opened" call
  /// rather than a queued capture.
  static const String openPrefix = 'open:';

  /// Records that the user opened a server save, for the sync loop to POST
  /// when a connection is back.
  ///
  /// Idempotent: re-opening while the row is still queued only refreshes it,
  /// so one detail visit offline stays exactly one retry later.
  Future<void> queueOpen(String itemId, {required String url}) => db.insert(
        'sync_queue',
        <String, Object?>{
          'client_id': '$openPrefix$itemId',
          'url': url,
          'captured_at': DateTime.now().toUtc().toIso8601String(),
          'status': 'pending',
          'kind': 'open',
        },
        conflictAlgorithm: ConflictAlgorithm.replace,
      );

  /// Server ids of opens still waiting for a connection.
  Future<List<String>> pendingOpens({int limit = queueBatchSize}) async {
    final rows = await db.query(
      'sync_queue',
      columns: const ['client_id'],
      where: "kind = 'open' AND status = 'pending'",
      orderBy: 'captured_at ASC',
      limit: limit,
    );
    return rows
        .map((row) => (row['client_id'] as String).substring(openPrefix.length))
        .toList(growable: false);
  }

  /// The server has the open, so the row has nothing left to say.
  ///
  /// The `kind` filter is belt-and-braces: the `open:` prefix already cannot
  /// collide with a capture's client id, but stating it keeps the invariant
  /// local to the statement that depends on it.
  Future<void> markOpenDone(String itemId) => db.delete('sync_queue',
      where: "client_id = ? AND kind = 'open'",
      whereArgs: <Object?>['$openPrefix$itemId']);


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
          <String, Object?>{'status': 'done', 'server_id': save.serverId},
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
  Future<List<SearchResult>> localSearch(String query, {int limit = 20, String? category, Map<String, String>? filters}) async {
    final escaped = query.replaceAll(r'\', r'\\').replaceAll('%', r'\%').replaceAll('_', r'\_');
    final like = '%$escaped%';
    final filtered = category != null && category != 'All' && category.isNotEmpty;
    final rows = await db.query(
      'items',
      where: r"(title LIKE ? ESCAPE '\' OR title_clean LIKE ? ESCAPE '\' "
          r"OR summary LIKE ? ESCAPE '\' OR tags LIKE ? ESCAPE '\')"
          '${filtered ? ' AND category = ?' : ''}',
      whereArgs: <Object?>[like, like, like, like, if (filtered) category],
      orderBy: 'created_at DESC',
      limit: filters?.isNotEmpty == true ? null : limit,
    );
    return rows.map(SearchResult.fromLocalRow)
        .where((row) => _matchesFilters(row, filters)).take(limit).toList(growable: false);
  }

  Future<void> upsertRemoteItems(List<ItemDetail> items) async {
    if (items.isEmpty) return;
    await db.transaction((txn) async {
      for (final ItemDetail item in items) {
        if (_hidden(item)) continue;
        await txn.insert('items', item.toLocalRow(), conflictAlgorithm: ConflictAlgorithm.replace);
      }
    });
  }

  Future<List<SearchResult>> recentLocalItems({int limit = 10, String? category, Map<String, String>? filters}) async {
    final filtered = category != null && category != 'All' && category.isNotEmpty;
    final rows = await db.query('items',
        where: filtered ? 'category = ?' : null,
        whereArgs: filtered ? <Object?>[category] : null,
        orderBy: 'created_at DESC', limit: filters?.isNotEmpty == true ? null : limit);
    return rows.map(SearchResult.fromLocalRow)
        .where((row) => _matchesFilters(row, filters)).take(limit).toList(growable: false);
  }

  /// The cached copy of one item, or null when it was never seen online.
  Future<ItemDetail?> localItem(String id) async {
    final rows = await db.query('items', where: 'id = ?', whereArgs: <Object?>[id], limit: 1);
    if (rows.isEmpty) return null;
    return ItemDetail.fromLocalRow(rows.first);
  }

  Future<String?> syncedItemId(String localId) async {
    final rows = await db.query('sync_queue', columns: ['server_id'],
        where: 'client_id = ? AND status = ?',
        whereArgs: [localId.substring('local-'.length), 'done'], limit: 1);
    return rows.isEmpty ? null : rows.single['server_id'] as String?;
  }

  Future<ItemDetail?> localItemForUrl(String url) async {
    final rows = await db.query('items', where: 'url = ? OR canonical_url = ?',
        whereArgs: <Object?>[url, url], limit: 1);
    return rows.isEmpty ? null : ItemDetail.fromLocalRow(rows.first);
  }

  Future<int> deleteItem(String id) => db.transaction((txn) async {
    final items = await txn.query('items', where: 'id = ?', whereArgs: [id]);
    if (items.isEmpty) return 0;
    final url = ItemDetail.fromLocalRow(items.single).canonicalUrl;
    for (final row in await txn.query('collections', where: 'deleted = 0')) {
      final urls = (jsonDecode(row['urls'] as String) as List).cast<String>();
      if (!urls.contains(url)) continue;
      urls.removeWhere((member) => member == url);
      await txn.rawUpdate('UPDATE collections SET urls = ?, dirty = 1, revision = revision + 1 WHERE id = ?',
        [jsonEncode(urls), row['id']]);
    }
    return txn.delete('items', where: 'id = ?', whereArgs: [id]);
  });

  /// Drops a queued capture the user deleted before it ever uploaded, so a
  /// cancelled save cannot resurrect itself on the next flush.
  Future<int> dropQueued(String clientId) =>
      db.delete('sync_queue', where: 'client_id = ?', whereArgs: <Object?>[clientId]);

  /// Captures the user still has not got onto the server.
  ///
  /// Parked (`failed`) rows are counted: they are the ones the user most needs
  /// to see, because nothing else will retry them. `pendingQueue` still drains
  /// only `pending`, so a badge that is non-zero while sync is idle means
  /// something is stuck rather than in flight.
  Future<int> pendingCount() async {
    final row = await db.rawQuery(
        'SELECT COUNT(*) AS c FROM sync_queue '
        "WHERE status IN ('pending', 'failed') AND kind = 'save'");
    return row.first['c'] as int? ?? 0;
  }
}


class LocalMemorySnapshot {
  const LocalMemorySnapshot(this.item, this.queue);
  final Map<String, Object?> item;
  final List<Map<String, Object?>> queue;
}
