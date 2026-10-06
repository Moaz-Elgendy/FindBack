import 'dart:io';
import 'package:findback/data/local_db.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  test('SQLite upgrade preserves version 1 items', () async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    final directory = await Directory.systemTemp.createTemp('findback-brief-');
    final path = '${directory.path}/legacy.db';
    final legacy = await openDatabase(path, version: 1, onCreate: (db, _) async {
      await db.execute('CREATE TABLE items (id TEXT PRIMARY KEY, url TEXT NOT NULL, summary TEXT)');
      await db.execute('CREATE TABLE sync_queue (client_id TEXT PRIMARY KEY, url TEXT NOT NULL, captured_at TEXT NOT NULL, status TEXT, retries INTEGER DEFAULT 0)');
      await db.insert('sync_queue', {'client_id': 'queued', 'url': 'https://example.test/queued', 'captured_at': '2026-10-06', 'status': 'pending'});
      await db.insert('items', <String, Object?>{'id': 'old', 'url': 'https://example.test', 'summary': 'Old facts'});
    });
    await legacy.close();
    final migrated = await LocalDb.openAt(path);
    try {
      final row = (await migrated.db.query('items')).single;
      expect(row['summary'], 'Old facts');
      expect(row['brief_payload'], '{}');
      expect(await migrated.db.getVersion(), LocalDb.schemaVersion);
      final queued = (await migrated.db.query('sync_queue')).single;
      expect(queued['client_id'], 'queued');
      expect(queued['status'], 'pending');
      expect(queued['server_id'], isNull);
    } finally {
      await migrated.close();
      await directory.delete(recursive: true);
    }
  });
}
