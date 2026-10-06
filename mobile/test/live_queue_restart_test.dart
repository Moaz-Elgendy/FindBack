// Opt-in: exercises the actual mobile services against the local Compose stack.
import 'dart:io';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _DevTokens extends TokenStore {
  @override
  Future<String?> read() async => null;
}

void main() {
  test('a real queued capture drains after Compose down/up and reuses its memory', () async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    final api = ApiClient(tokens: _DevTokens());
    var stackStopped = false;
    Future<void> compose(List<String> args) async {
      final result = await Process.run('docker', ['compose', ...args], workingDirectory: '..');
      if (result.exitCode != 0) throw StateError('Compose failed: ${result.stderr}');
    }
    Future<void> waitForApi() async {
      final deadline = DateTime.now().add(const Duration(seconds: 60));
      while (DateTime.now().isBefore(deadline)) {
        try { await api.listItems(limit: 1); return; } on ApiException { /* startup */ }
        await Future<void>.delayed(const Duration(seconds: 1));
      }
      throw StateError('API did not recover within 60 seconds');
    }
    try {
      final before = await api.listItems(limit: 20);
      final existing = before.items.firstWhere((item) => item.url.contains('19PXq2Y3AR'));
      final queuedUrl = Uri.parse(existing.url).replace(queryParameters: {
        ...Uri.parse(existing.url).queryParameters, 'utm_source': 'findback_restart_regression',
      }).toString();
      await compose(['down']);
      stackStopped = true;
      final capture = CaptureService.of(api: api, db: db, isOnline: () async => true);
      final saved = await capture.capture(url: queuedUrl);
      expect(saved.isQueued, isTrue);
      expect(await db.pendingCount(), 1);
      final sync = SyncService(pending: db.pendingQueue, send: api.syncBatch,
          apply: db.applyMapped, markFailed: db.markQueueFailed, isOnline: () async => true);
      expect(await sync.flush(), 0);
      expect(await db.pendingCount(), 1, reason: 'backend outage cannot discard a capture');
      expect(sync.lastError.value, isNotNull);
      await compose(['up', '-d']);
      stackStopped = false;
      await waitForApi();
      expect(await sync.flush(force: true), 1);
      expect(await db.pendingCount(), 0);
      expect(await db.syncedItemId(saved.reference), existing.id);
      expect(sync.lastError.value, isNull);
      expect((await api.getItem(existing.id)).isReady, isTrue);
      expect((await api.listItems(limit: 20)).items.map((item) => item.id),
          unorderedEquals(before.items.map((item) => item.id)));
      stdout.writeln('LIVE VERIFIED: queued during outage; Compose down/up; uploaded to same READY memory; no duplicate item.');
    } finally {
      if (stackStopped) await compose(['up', '-d']);
      api.close();
      await db.close();
    }
  }, skip: Platform.environment['FINDBACK_LIVE_QUEUE_TEST'] != '1',
      timeout: const Timeout(Duration(minutes: 2)));
}
