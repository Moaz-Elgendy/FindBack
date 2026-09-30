import 'dart:async';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';

typedef PendingLoader = Future<List<SyncItem>> Function();
typedef BatchSender = Future<SyncBatchResult> Function(List<SyncItem>);
typedef MappedApplier = Future<void> Function(List<MappedSave>);
typedef FailureMarker = Future<void> Function(String clientId);
typedef ConnectivityProbe = Future<bool> Function();
typedef ConnectivityChanges = Stream<bool> Function();

bool anyConnection(List<ConnectivityResult> results) =>
    results.any((ConnectivityResult result) => result != ConnectivityResult.none);

Future<bool> systemIsOnline() async => anyConnection(await Connectivity().checkConnectivity());

ConnectivityChanges systemConnectivityChanges() =>
    () => Connectivity().onConnectivityChanged.map(anyConnection);

/// Drains the offline write queue to `POST /api/v1/sync/batch`.
///
/// Dependencies are function-typed so the whole thing runs in a plain `flutter
/// test` with fakes — no emulator, no server.
class SyncService {
  SyncService({
    required PendingLoader pending,
    required BatchSender send,
    required MappedApplier apply,
    required FailureMarker markFailed,
    ConnectivityProbe? isOnline,
    ConnectivityChanges? changes,
    Duration retryCeiling = const Duration(minutes: 5),
    Duration retryBase = const Duration(seconds: 1),
  })  : _pending = pending,
        _send = send,
        _apply = apply,
        _markFailed = markFailed,
        _isOnline = isOnline ?? systemIsOnline,
        _changes = changes ?? systemConnectivityChanges(),
        _retryCeiling = retryCeiling,
        _retryBase = retryBase;

  factory SyncService.of({required LocalDb db, required ApiClient api}) => SyncService(
        pending: db.pendingQueue,
        send: api.syncBatch,
        apply: db.applyMapped,
        markFailed: db.markQueueFailed,
      );

  static const Duration heartbeat = Duration(seconds: 30);

  final PendingLoader _pending;
  final BatchSender _send;
  final MappedApplier _apply;
  final FailureMarker _markFailed;
  final ConnectivityProbe _isOnline;
  final ConnectivityChanges _changes;
  final Duration _retryCeiling;
  final Duration _retryBase;

  bool _flushing = false;
  Duration _retryIn = Duration.zero;
  DateTime _nextAttemptAt = DateTime.fromMillisecondsSinceEpoch(0);
  StreamSubscription<bool>? _subscription;
  Timer? _heartbeat;
  void Function(int flushed)? _onFlushed;

  Duration get retryDelay => _retryIn;

  /// Sends everything currently queued. Never throws: callers include a timer
  /// and a connectivity stream, neither of which has anyone to report to.
  Future<int> flush() async {
    if (_flushing) return 0;
    if (DateTime.now().isBefore(_nextAttemptAt)) return 0;
    _flushing = true;
    try {
      if (!await _isOnline()) return 0;
      final queue = await _pending();
      if (queue.isEmpty) return 0;

      final result = await _send(queue);
      await _apply(result.mapped);
      for (final String clientId in result.failedClientIds) {
        await _markFailed(clientId);
      }
      _resetRetry();
      final flushed = result.mapped.length;
      if (flushed > 0) _onFlushed?.call(flushed);
      return flushed;
    } on ApiException catch (error) {
      _backOff();
      debugPrint('[sync] batch rejected (${error.kind.name}): ${error.message}');
      return 0;
    } catch (error) {
      _backOff();
      debugPrint('[sync] flush failed: $error');
      return 0;
    } finally {
      _flushing = false;
    }
  }

  /// Flushes on launch, on every connectivity transition, and on a heartbeat.
  void start({void Function(int flushed)? onFlushed, Duration interval = heartbeat}) {
    _onFlushed = onFlushed;
    _subscription ??= _changes().listen((bool online) {
      if (online) flush();
    });
    _heartbeat ??= Timer.periodic(interval, (_) => flush());
    flush();
  }

  Future<void> stop() async {
    await _subscription?.cancel();
    _subscription = null;
    _heartbeat?.cancel();
    _heartbeat = null;
    _onFlushed = null;
  }

  /// Doubling from 1s to 5min. The RN client computed this backoff but the 30s
  /// timer ignored it, so a downed API was retried at full rate forever.
  void _backOff() {
    _retryIn = _retryIn <= Duration.zero ? _retryBase : _retryIn * 2;
    if (_retryIn > _retryCeiling) _retryIn = _retryCeiling;
    _nextAttemptAt = DateTime.now().add(_retryIn);
  }

  void _resetRetry() {
    _retryIn = Duration.zero;
    _nextAttemptAt = DateTime.fromMillisecondsSinceEpoch(0);
  }
}
