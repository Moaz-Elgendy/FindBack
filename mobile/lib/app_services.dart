import 'dart:async';

import 'package:flutter/foundation.dart';

import 'data/api_client.dart';
import 'data/local_db.dart';
import 'services/capture_service.dart';
import 'services/items_service.dart';
import 'services/share_intent_service.dart';
import 'services/sync_service.dart';

/// Composition root. Built once in `main`, handed to the widget tree, and torn
/// down together — no service locator, so tests can build a subset.
class AppServices {
  AppServices({
    required this.db,
    required this.api,
    required this.sync,
    required this.capture,
    required this.items,
    required this.share,
  });

  static Future<AppServices> create({LocalDb? database}) async {
    final LocalDb db = database ?? await LocalDb.open();
    final ApiClient api = ApiClient();
    final CaptureService capture = CaptureService.of(db: db, api: api);
    return AppServices(
      db: db,
      api: api,
      sync: SyncService.of(db: db, api: api),
      capture: capture,
      items: ItemsService.of(db: db, api: api),
      share: ShareIntentService(),
    );
  }

  final LocalDb db;
  final ApiClient api;
  final SyncService sync;
  final CaptureService capture;
  final ItemsService items;

  /// The Share Sheet entry point: Any app -> Share -> FindBack.
  final ShareIntentService share;

  /// Queue depth, shown as a badge so "saved on this device" is never silent.
  final ValueNotifier<int> pending = ValueNotifier<int>(0);
  final ValueNotifier<CaptureBatch?> sharedCapture = ValueNotifier(null);

  Future<void> refreshPending() async {
    try {
      pending.value = await db.pendingCount();
    } catch (error) {
      debugPrint('[services] pending count failed: $error');
    }
  }

  /// Starts the queue drainer and keeps the badge in step with it.
  Future<void> startSync() async {
    await refreshPending();
    sync.start(onFlushed: (int flushed) => refreshPending());
  }

  /// Handles anything the Share Sheet sends, on launch and afterwards.
  ///
  /// A share is just another way to press Save, so it goes through the same
  /// capture path -- including the offline path. That is the whole reason a
  /// capture cannot be lost without internet: sharing adds no new failure mode.
  Future<void> startShareHandling() async {
    _shareSubscription ??= share.shares.listen((String payload) async {
      try {
        final CaptureBatch? outcome =
            await share.captureShared(payload, capture);
        // A share that had to wait for the network changes the pending count.
        if (outcome?.isQueued ?? false) await refreshPending();
        if (outcome != null) sharedCapture.value = outcome;
      } catch (error) {
        // The user shared a link the server actively refused. Swallowing it
        // would be a lie, but crashing the app over it is worse; the capture
        // path already logs it.
        debugPrint('[services] share could not be saved: $error');
      }
    });
    // The share that started this launch.
    try {
      final CaptureBatch? outcome =
          await share.captureInitialShare(capture);
      if (outcome?.isQueued ?? false) await refreshPending();
      if (outcome != null) sharedCapture.value = outcome;
    } catch (error) {
      debugPrint('[services] initial share could not be saved: $error');
    }
  }

  StreamSubscription<String>? _shareSubscription;

  Future<void> dispose() async {
    await _shareSubscription?.cancel();
    _shareSubscription = null;
    await share.dispose();
    await sync.stop();
    api.close();
    await db.close();
    pending.dispose();
    sharedCapture.dispose();
  }
}
