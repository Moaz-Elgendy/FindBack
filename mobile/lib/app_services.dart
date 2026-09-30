import 'package:flutter/foundation.dart';

import 'data/api_client.dart';
import 'data/local_db.dart';
import 'services/capture_service.dart';
import 'services/items_service.dart';
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
  });

  static Future<AppServices> create({LocalDb? database}) async {
    final LocalDb db = database ?? await LocalDb.open();
    final ApiClient api = ApiClient();
    return AppServices(
      db: db,
      api: api,
      sync: SyncService.of(db: db, api: api),
      capture: CaptureService.of(db: db, api: api),
      items: ItemsService.of(db: db, api: api),
    );
  }

  final LocalDb db;
  final ApiClient api;
  final SyncService sync;
  final CaptureService capture;
  final ItemsService items;

  /// Queue depth, shown as a badge so "saved on this device" is never silent.
  final ValueNotifier<int> pending = ValueNotifier<int>(0);

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

  Future<void> dispose() async {
    await sync.stop();
    api.close();
    await db.close();
    pending.dispose();
  }
}
