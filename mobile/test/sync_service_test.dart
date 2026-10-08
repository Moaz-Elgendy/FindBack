import 'dart:async';

import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';

SyncItem _item(String clientId) => SyncItem(
      clientId: clientId,
      url: 'https://example.com/$clientId',
      capturedAt: '2026-09-29T10:00:00.000Z',
    );

class _Recorder {
  final List<List<MappedSave>> applied = <List<MappedSave>>[];
  final List<String> failed = <String>[];
  final List<SyncItem> sent = <SyncItem>[];

  Future<void> apply(List<MappedSave> mapped) async => applied.add(mapped);
  Future<void> markFailed(String clientId) async => failed.add(clientId);
}

void main() {
  test('stop waits for an in-flight memory action', () async {
    final started = Completer<void>();
    final release = Completer<void>();
    final sync = SyncService(pending: () async => [],
      send: (_) async => const SyncBatchResult(mapped: [], failedClientIds: []),
      apply: (_) async {}, markFailed: (_) async {}, isOnline: () async => false);
    final action = sync.exclusive(() async { started.complete(); await release.future; });
    await started.future;
    var stopped = false;
    final stopping = sync.stop().then((_) => stopped = true);
    await Future<void>.delayed(Duration.zero);
    expect(stopped, isFalse);
    release.complete();
    await action; await stopping;
    expect(stopped, isTrue);
  });

  test('does not talk to the API while offline', () async {
    final _Recorder rec = _Recorder();
    int sends = 0;
    final SyncService sync = SyncService(
      pending: () async => <SyncItem>[_item('a')],
      send: (List<SyncItem> items) async {
        sends++;
        return const SyncBatchResult(mapped: <MappedSave>[], failedClientIds: <String>[]);
      },
      apply: rec.apply,
      markFailed: rec.markFailed,
      isOnline: () async => false,
    );

    expect(await sync.flush(), 0);
    expect(sends, 0);
  });

  test('an empty queue is not worth a request', () async {
    int sends = 0;
    final SyncService sync = SyncService(
      pending: () async => const <SyncItem>[],
      send: (List<SyncItem> items) async {
        sends++;
        return const SyncBatchResult(mapped: <MappedSave>[], failedClientIds: <String>[]);
      },
      apply: _Recorder().apply,
      markFailed: (_) async {},
      isOnline: () async => true,
    );
    expect(await sync.flush(), 0);
    expect(sends, 0);
  });

  test('maps what landed and counts what the server refused', () async {
    final _Recorder rec = _Recorder();
    final SyncService sync = SyncService(
      pending: () async => <SyncItem>[_item('c1'), _item('c2')],
      send: (List<SyncItem> items) async {
        rec.sent.addAll(items);
        return const SyncBatchResult(
          mapped: <MappedSave>[MappedSave(clientId: 'c1', serverId: 'uuid-1')],
          failedClientIds: <String>['c2'],
        );
      },
      apply: rec.apply,
      markFailed: rec.markFailed,
      isOnline: () async => true,
    );

    expect(await sync.flush(), 1);
    expect(rec.sent.map((SyncItem item) => item.clientId), <String>['c1', 'c2']);
    expect(rec.applied.single.single.serverId, 'uuid-1');
    expect(rec.failed, <String>['c2']);
  });

  test('one send per flush, however many triggers start it', () async {
    int sends = 0;
    final SyncService sync = SyncService(
      pending: () async {
        await Future<void>.delayed(const Duration(milliseconds: 20));
        return <SyncItem>[_item('a')];
      },
      send: (List<SyncItem> items) async {
        sends++;
        await Future<void>.delayed(const Duration(milliseconds: 20));
        return const SyncBatchResult(mapped: <MappedSave>[], failedClientIds: <String>[]);
      },
      apply: _Recorder().apply,
      markFailed: (_) async {},
      isOnline: () async => true,
    );

    final List<int> results = await Future.wait<int>(<Future<int>>[
      sync.flush(),
      sync.flush(),
      sync.flush(),
    ]);
    expect(sends, 1);
    expect(results, <int>[0, 0, 0]);
  });

  group('retry backoff', () {
    test('grows on failure and actually suppresses the next attempt', () async {
      int sends = 0;
      final SyncService sync = SyncService(
        pending: () async => <SyncItem>[_item('a')],
        send: (List<SyncItem> items) async {
          sends++;
          throw ApiException('upstream exploded',
              statusCode: 503, kind: ApiFailureKind.server);
        },
        apply: _Recorder().apply,
        markFailed: (_) async {},
        isOnline: () async => true,
        retryBase: const Duration(milliseconds: 60),
      );

      expect(await sync.flush(), 0);
      expect(sends, 1);
      expect(sync.retryDelay, const Duration(milliseconds: 60));

      // The 30s heartbeat calls this again immediately; the window must hold.
      expect(await sync.flush(), 0);
      expect(sends, 1);

      await Future<void>.delayed(const Duration(milliseconds: 80));
      await sync.flush();
      expect(sends, 2);
      expect(sync.retryDelay, const Duration(milliseconds: 120));
    });

    test('never grows past the ceiling', () async {
      final SyncService sync = SyncService(
        pending: () async => <SyncItem>[_item('a')],
        send: (List<SyncItem> items) async => throw ApiException('no route'),
        apply: _Recorder().apply,
        markFailed: (_) async {},
        isOnline: () async => true,
        retryBase: const Duration(days: 4),
        retryCeiling: const Duration(days: 2),
      );
      await sync.flush();
      expect(sync.retryDelay, const Duration(days: 2));
    });

    test('resets after a successful flush', () async {
      bool broken = true;
      final SyncService sync = SyncService(
        pending: () async => <SyncItem>[_item('a')],
        send: (List<SyncItem> items) async {
          if (broken) {
            broken = false;
            throw ApiException('offline');
          }
          return const SyncBatchResult(
            mapped: <MappedSave>[MappedSave(clientId: 'a', serverId: 'uuid-a')],
            failedClientIds: <String>[],
          );
        },
        apply: _Recorder().apply,
        markFailed: (_) async {},
        isOnline: () async => true,
        retryBase: const Duration(milliseconds: 40),
      );

      await sync.flush();
      expect(sync.retryDelay, const Duration(milliseconds: 40));
      await Future<void>.delayed(const Duration(milliseconds: 60));
      expect(await sync.flush(), 1);
      expect(sync.retryDelay, Duration.zero);
    });
  });

  test('a row the server refuses is flagged, not silently dropped', () async {
    final _Recorder rec = _Recorder();
    final SyncService sync = SyncService(
      pending: () async => <SyncItem>[_item('a')],
      send: (List<SyncItem> items) async => const SyncBatchResult(
        mapped: <MappedSave>[],
        failedClientIds: <String>['a'],
      ),
      apply: rec.apply,
      markFailed: rec.markFailed,
      isOnline: () async => true,
    );

    expect(await sync.flush(), 0);
    expect(rec.failed, <String>['a']);
    // The caller still owns the row, so the next heartbeat tries it again.
    expect(await sync.flush(), 0);
    expect(rec.failed, <String>['a', 'a']);
  });

  test('a bad local write never crashes the drainer', () async {
    final SyncService sync = SyncService(
      pending: () async => <SyncItem>[_item('a')],
      send: (List<SyncItem> items) async => const SyncBatchResult(
        mapped: <MappedSave>[MappedSave(clientId: 'a', serverId: 'uuid-a')],
        failedClientIds: <String>[],
      ),
      apply: (List<MappedSave> mapped) async => throw StateError('database is locked'),
      markFailed: (_) async {},
      isOnline: () async => true,
    );
    expect(await sync.flush(), 0);
  });

  // --- Phase 15: the capture is never lost without internet ---------------

  test('a capture taken offline is sent once the network comes back', () async {
    // The whole offline contract in one test: nothing is sent while there is no
    // connection, and the moment there is one, the queued capture goes.
    final _Recorder rec = _Recorder();
    bool online = false;
    final StreamController<bool> connectivity =
        StreamController<bool>.broadcast();
    addTearDown(connectivity.close);

    final SyncService sync = SyncService(
      pending: () async => online ? <SyncItem>[_item('c1')] : <SyncItem>[],
      send: (List<SyncItem> batch) async {
        rec.sent.addAll(batch);
        return SyncBatchResult(
          mapped: <MappedSave>[
            MappedSave(clientId: batch.single.clientId, serverId: 'server-1'),
          ],
          failedClientIds: const <String>[],
        );
      },
      apply: rec.apply,
      markFailed: rec.markFailed,
      isOnline: () async => online,
      changes: () => connectivity.stream,
    );
    addTearDown(sync.stop);

    // Offline: the capture waits, and the API is never touched.
    expect(await sync.flush(), 0);
    expect(rec.sent, isEmpty, reason: 'no request may be made while offline');

    sync.start();

    // Reconnect.
    online = true;
    connectivity.add(true);
    await Future<void>.delayed(Duration.zero);

    expect(rec.sent.single.clientId, 'c1');
    expect(rec.applied.single.single.serverId, 'server-1');
  });

  test('a capture saved while offline is still sent by the heartbeat', () async {
    // Reconnect is not the only trigger: the timer is what saves a capture when
    // the app is left open after the network comes back.
    final _Recorder rec = _Recorder();
    final SyncService sync = SyncService(
      pending: () async => <SyncItem>[_item('c2')],
      send: (List<SyncItem> batch) async {
        rec.sent.addAll(batch);
        return SyncBatchResult(
          mapped: <MappedSave>[
            MappedSave(clientId: batch.single.clientId, serverId: 'server-2'),
          ],
          failedClientIds: const <String>[],
        );
      },
      apply: rec.apply,
      markFailed: rec.markFailed,
      isOnline: () async => true,
      changes: () => const Stream<bool>.empty(),
    );
    addTearDown(sync.stop);

    sync.start(interval: const Duration(milliseconds: 10));
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(rec.sent.map((SyncItem s) => s.clientId), contains('c2'));
  });
}
