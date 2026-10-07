import 'dart:io';
import 'package:findback/data/local_db.dart';
import 'package:findback/services/sync_service.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:flutter/material.dart';
import 'package:findback/features/home/capture_sheet.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/models/item.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/utils/share_text.dart';
void main() {
  test('twenty queued links survive restart and upload through existing batch sync', () async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    final dir = await Directory.systemTemp.createTemp('findback-multiple-');
    final path = '${dir.path}/queue.db';
    var db = await LocalDb.openAt(path);
    final capture = CaptureService(isOnline: () async => throw StateError('must queue before network'),
      ingest: (_, __, ___) async => throw StateError('must queue'),
      queue: (url, preview, hint) => db.queueSave(url: url, preview: preview, titleHint: hint),
      existingItem: db.localItemForUrl);
    final payload = List.generate(20, (i) => 'https://example.com/$i').join('\n');
    expect((await capture.captureText(payload)).outcomes.length, 20);
    // Reproduce the phone's v3 database with the missing sync mapping column.
    await db.db.execute('ALTER TABLE sync_queue DROP COLUMN server_id');
    await db.db.execute('PRAGMA user_version = 3');
    await db.close();
    db = await LocalDb.openAt(path);
    try {
      expect(await db.pendingCount(), 20);
      final sync = SyncService(pending: db.pendingQueue,
        send: (items) async {
          expect(items.length, 20);
          return SyncBatchResult(mapped: [for (final item in items) MappedSave(clientId: item.clientId, serverId: 'server-${item.clientId}')], failedClientIds: []);
        }, apply: db.applyMapped, markFailed: db.markQueueFailed,
        isOnline: () async => true);
      expect(await sync.flush(force: true), 20);
      expect(await db.pendingCount(), 0);
      await sync.stop();
    } finally {
      await db.close();
      await dir.delete(recursive: true);
    }
  });
  testWidgets('paste saves multiple links and retains only failed links for retry', (tester) async {
    CaptureBatch? saved;
    final capture = CaptureService(isOnline: () async => true,
      ingest: (url, _, __) async {
        if (url.endsWith('/bad')) throw ApiException('Rejected', kind: ApiFailureKind.rejected);
        return IngestResult(id: url, canonicalUrl: url, status: 'pending');
      }, queue: (url, _, __) async { if (url.endsWith('/bad')) throw StateError('Storage failed'); return url; });
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: CaptureSheet(capture: capture, onSaved: (batch) => saved = batch))));
    await tester.enterText(find.byType(TextField).first, 'https://example.com/good https://example.com/bad');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(saved!.outcomes.length, 1);
    expect(find.textContaining('1 could not be saved'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField).first).controller!.text, 'https://example.com/bad');
  });
  test('initial and live shares capture all twenty links', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    const channel = MethodChannel(ShareIntentService.channelName);
    final payload = List.generate(20, (i) => 'https://example.com/$i').join('\n');
    final saved = <String>[];
    final capture = CaptureService(isOnline: () async => false,
      ingest: (_, __, ___) async => throw StateError('offline'),
      queue: (url, _, __) async { saved.add(url); return url; });
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (_) async => payload);
    final share = ShareIntentService(channel: channel);
    final initial = await share.captureInitialShare(capture);
    expect(initial!.outcomes.length, 20);
    final done = share.shares.first.then((text) => share.captureShared(text, capture));
    await TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.handlePlatformMessage(
      ShareIntentService.channelName, const StandardMethodCodec().encodeMethodCall(MethodCall('onShare', payload)), (_) {});
    expect((await done)!.outcomes.length, 20);
    expect(saved.length, 40);
    await share.dispose();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, null);
  });
  test('extracts distinct links with punctuation and bare domains', () {
    expect(extractUrlsFromShareText('Read (https://a.test/x), https://b.test/y. https://a.test/x\nexample.com/z'), ['https://a.test/x', 'https://b.test/y', 'https://example.com/z']);
  });
  test('queues twenty unique links offline', () async {
    final queued = <String>[];
    final capture = CaptureService(isOnline: () async => false,
      ingest: (_, __, ___) async => throw StateError('offline'),
      queue: (url, preview, hint) async { expect(preview, url); queued.add(url); return '${queued.length}'; });
    final links = List.generate(20, (i) => 'https://example.com/$i');
    final result = await capture.captureText('${links.join('\n')}\n${links.first}');
    expect(queued, links); expect(result.outcomes.length, 20); expect(result.failedUrls, isEmpty);
  });
  test('one storage failure does not discard the rest', () async {
    final capture = CaptureService(isOnline: () async => true,
      ingest: (url, _, __) async { if (url.endsWith('/bad')) throw ApiException('Rejected', statusCode: 400, kind: ApiFailureKind.rejected); return IngestResult(id: url, canonicalUrl: url, status: 'pending'); },
      queue: (url, _, __) async { if (url.endsWith('/bad')) throw StateError('Storage failed'); return url; });
    final result = await capture.captureText('https://example.com/one https://example.com/bad https://example.com/two');
    expect(result.outcomes.length, 2); expect(result.failedUrls, ['https://example.com/bad']);
  });
}
