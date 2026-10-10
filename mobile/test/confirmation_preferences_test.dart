import 'dart:io';
import 'package:findback/data/local_db.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  test('v7 upgrade preserves queued saves and persists confirmation reset',
      () async {
    final directory =
        await Directory.systemTemp.createTemp('findback-confirm-');
    final path = '${directory.path}/library.db';
    var db = await LocalDb.openAt(path);
    try {
      await db.db.execute('DROP TABLE IF EXISTS preferences');
      await db.db.setVersion(7);
      await db.queueSave(url: 'https://example.com/preserved');
      await db.close();
      db = await LocalDb.openAt(path);
      expect(await db.deleteConfirmationSuppressed, isFalse);
      expect(await db.db.query('sync_queue'), hasLength(1));
      expect(await db.db.query('items'), hasLength(1));
      await db.setDeleteConfirmationSuppressed(true);
      await db.close();
      db = await LocalDb.openAt(path);
      expect(await db.deleteConfirmationSuppressed, isTrue);
      await db.setDeleteConfirmationSuppressed(false);
      expect(await db.deleteConfirmationSuppressed, isFalse);
    } finally {
      await db.close();
      await directory.delete(recursive: true);
    }
  });
}
