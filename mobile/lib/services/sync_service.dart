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
typedef PendingOpensLoader = Future<List<String>> Function();
typedef OpenSender = Future<void> Function(String itemId);
typedef OpenDoneMarker = Future<void> Function(String itemId);

bool anyConnection(List<ConnectivityResult> results) =>
    results.any((ConnectivityResult result) => result != ConnectivityResult.none);

Future<bool> systemIsOnline() async => anyConnection(await Connectivity().checkConnectivity());

ConnectivityChanges systemConnectivityChanges() =>
    () => Connectivity().onConnectivityChanged.map(anyConnection);

/// Drains the offline write queue: captures to `POST /api/v1/sync/batch`,
/// queued mark-opened calls to `POST /api/v1/items/{id}/open`.
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
    PendingOpensLoader? pendingOpens,
    OpenSender? sendOpen,
    OpenDoneMarker? markOpenDone,
    Duration heartbeatInterval = heartbeat,
    Duration retryCeiling = const Duration(minutes: 5),
    Duration retryBase = const Duration(seconds: 1),
  })  : _pending = pending,
        _send = send,
        _apply = apply,
        _markFailed = markFailed,
        _isOnline = isOnline ?? systemIsOnline,
        _changes = changes ?? systemConnectivityChanges(),
        _pendingOpens = pendingOpens ?? _noOpens,
        _sendOpen = sendOpen,
        _markOpenDone = markOpenDone,
        _heartbeatInterval = heartbeatInterval,
        _retryCeiling = retryCeiling,
        _retryBase = retryBase;

  factory SyncService.of({required LocalDb db, required ApiClient api}) => SyncService(
        pending: db.pendingQueue,
        send: api.syncBatch,
        apply: db.applyMapped,
        markFailed: db.markQueueFailed,
        pendingOpens: db.pendingOpens,
        sendOpen: api.markOpened,
        markOpenDone: db.markOpenDone,
      );

  static const Duration heartbeat = Duration(seconds: 30);

  static Future<List<String>> _noOpens() async => const <String>[];

  final PendingLoader _pending;
  final BatchSender _send;
  final MappedApplier _apply;
  final FailureMarker _markFailed;
  final PendingOpensLoader _pendingOpens;
  final OpenSender? _sendOpen;
  final OpenDoneMarker? _markOpenDone;
  final ConnectivityProbe _isOnline;
  final ConnectivityChanges _changes;
  // The heartbeat interval is configurable so a test can drive the timer rather
  // than wait 30 seconds for it.
  final Duration _heartbeatInterval;
  final Duration _retryCeiling;
  final Duration _retryBase;

  bool _flushing = false;
  int _exclusiveCount = 0;
  Future<void> _exclusiveTail = Future.value();

  Future<T> exclusive<T>(Future<T> Function() work) async {
    _exclusiveCount++;
    final previous = _exclusiveTail;
    final done = Completer<void>();
    _exclusiveTail = done.future;
    try {
      await previous;
      await _flushDone?.future;
      return await work();
    } finally {
      _exclusiveCount--;
      done.complete();
    }
  }

  Completer<void>? _flushDone;
  Duration _retryIn = Duration.zero;
  DateTime _nextAttemptAt = DateTime.fromMillisecondsSinceEpoch(0);
  StreamSubscription<bool>? _subscription;
  Timer? _heartbeat;
  void Function(int flushed)? _onFlushed;

  Duration get retryDelay => _retryIn;
  final ValueNotifier<ApiException?> lastError = ValueNotifier(null);

  DateTime _serverRetryAt = DateTime.fromMillisecondsSinceEpoch(0);
  bool get canUpload => !DateTime.now().isBefore(_serverRetryAt);

  void deferAfter(ApiException error) {
    lastError.value = error;
    _backOff(minimumSeconds: error.retryAfterSeconds);
  }

  /// Sends everything currently queued. Never throws: callers include a timer
  /// and a connectivity stream, neither of which has anyone to report to.
  Future<int> flush({bool force = false}) async {
    if (_flushing || _exclusiveCount > 0 || !canUpload) return 0;
    if (!force && DateTime.now().isBefore(_nextAttemptAt)) return 0;
    _flushing = true;
    _flushDone = Completer<void>();
    try {
      if (!await _isOnline()) {
        lastError.value = null;
        return 0;
      }
      final queue = await _pending();
      final opens = _sendOpen == null || _markOpenDone == null
          ? const <String>[]
          : await _pendingOpens();
      if (queue.isEmpty && opens.isEmpty) {
        lastError.value = null;
        return 0;
      }

      int flushed = 0;
      if (queue.isNotEmpty) {
        final result = await _send(queue);
        await _apply(result.mapped);
        for (final String clientId in result.failedClientIds) {
          await _markFailed(clientId);
        }
        flushed = result.mapped.length;
        if (flushed > 0) _onFlushed?.call(flushed);
      }
      // Mark-opened calls ride the same triggers, backoff and parking rules
      // as captures: one POST per id, and the row is dropped only when the
      // server has it. Each id is caught on its own so one bad row cannot
      // strand the ones behind it; a failure still backs off and surfaces,
      // and the rows that did land stay landed.
      ApiException? openFailure;
      Object? openError;
      for (final String itemId in opens) {
        try {
          await _sendOpen!(itemId);
          await _markOpenDone!(itemId);
        } on ApiException catch (error) {
          if (error.statusCode == 404 || error.statusCode == 410) {
            await _markOpenDone!(itemId);
            continue;
          }
          openFailure ??= error;
          debugPrint('[sync] mark-opened rejected (${error.kind.name}) for $itemId: ${error.message}');
        } catch (error) {
          openError ??= error;
          debugPrint('[sync] mark-opened failed for $itemId: $error');
        }
      }
      if (openFailure != null) {
        lastError.value = openFailure;
        _backOff(minimumSeconds: openFailure.retryAfterSeconds);
        return flushed;
      }
      if (openError != null) {
        _backOff();
        return flushed;
      }
      _resetRetry();
      lastError.value = null;
      return flushed;
    } on ApiException catch (error) {
      lastError.value = error;
      _backOff(minimumSeconds: error.retryAfterSeconds);
      debugPrint('[sync] batch rejected (${error.kind.name}): ${error.message}');
      return 0;
    } catch (error) {
      _backOff();
      debugPrint('[sync] flush failed: $error');
      return 0;
    } finally {
      _flushing = false;
      _flushDone?.complete();
      _flushDone = null;
    }
  }

  /// Flushes on launch, on every connectivity transition, and on a heartbeat.
  void start({void Function(int flushed)? onFlushed, Duration? interval}) {
    _onFlushed = onFlushed;
    _subscription ??= _changes().listen((bool online) {
      if (online) flush();
    });
    _heartbeat ??= Timer.periodic(interval ?? _heartbeatInterval, (_) => flush());
    flush();
  }

  Future<void> stop() async {
    await _subscription?.cancel();
    _subscription = null;
    _heartbeat?.cancel();
    _heartbeat = null;
    _onFlushed = null;
    await _flushDone?.future;
    if (_exclusiveCount > 0) await _exclusiveTail;
  }

  /// Doubling from 1s to 5min. The RN client computed this backoff but the 30s
  /// timer ignored it, so a downed API was retried at full rate forever.
  void _backOff({int? minimumSeconds}) {
    if (minimumSeconds != null) {
      final deadline = DateTime.now().add(Duration(seconds: minimumSeconds));
      if (deadline.isAfter(_serverRetryAt)) _serverRetryAt = deadline;
    }
    _retryIn = _retryIn <= Duration.zero ? _retryBase : _retryIn * 2;
    if (_retryIn > _retryCeiling) _retryIn = _retryCeiling;
    if (minimumSeconds != null && Duration(seconds: minimumSeconds) > _retryIn) {
      _retryIn = Duration(seconds: minimumSeconds);
    }
    _nextAttemptAt = DateTime.now().add(_retryIn);
  }

  void _resetRetry() {
    _retryIn = Duration.zero;
    _nextAttemptAt = DateTime.fromMillisecondsSinceEpoch(0);
  }
}
