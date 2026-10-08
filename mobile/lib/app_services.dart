import 'dart:async';

import 'package:flutter/foundation.dart';

import 'data/api_client.dart';
import 'data/local_db.dart';
import 'data/token_store.dart';
import 'models/item.dart';
import 'models/search_result.dart';
import 'services/guest_library.dart';
import 'services/collections_service.dart';
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
    this.guest = false,
    this.guestLibrary,
    this.initialLibrary = const [],
  });

  static Future<AppServices> create({LocalDb? database, TokenStore? tokens, bool guest = false}) async {
    final LocalDb db = database ?? await LocalDb.open();
    final ApiClient api = ApiClient(tokens: tokens);
    final CaptureService capture = guest ? CaptureService(
      ingest: (url, preview, hint) async {
        final saved = await db.localItemForUrl(url);
        if (saved != null && !saved.id.startsWith('local-')) {
          return IngestResult(id: saved.id, status: saved.status,
              canonicalUrl: saved.canonicalUrl, alreadyExists: true);
        }
        return api.ingestUrl(url, preview: preview, titleHint: hint);
      },
      queue: (url, preview, hint) => db.queueSave(url: url, preview: preview, titleHint: hint),
      existingItem: db.localItemForUrl,
    ) : CaptureService.of(db: db, api: api);
    final guestStore = guest ? GuestLibrary(db, api) : null;
    return AppServices(
      db: db,
      api: api,
      sync: SyncService.of(db: db, api: api),
      capture: capture,
      items: guestStore?.items ?? ItemsService.of(db: db, api: api),
      guestLibrary: guestStore,
      guest: guest,
      initialLibrary: await db.recentLocalItems(limit: 20),
      share: ShareIntentService(),
    );
  }

  late final CollectionsService collections = CollectionsService(db, api, guest: guest);
  Future<void>? _collectionWork;

  bool _collectionsActive = false;
  Future<void> refreshCollections() {
    if (_accountWorkStopped) return Future.value();
    _collectionsActive = true;
    return _collectionWork = _syncCollections();
  }

  Future<void> _syncCollections() async {
    try { await collections.refresh(); }
    on ApiException catch (error) { debugPrint('[collections] sync failed: ${error.kind.name}'); }
  }

  final bool guest;
  final List<SearchResult> initialLibrary;
  final GuestLibrary? guestLibrary;
  bool _accountWorkStopped = false;
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
  Future<void> refreshGuest() async {
    try { await guestLibrary?.refresh(); }
    on ApiException catch (failure) {
      if (!failure.isRetryableOffline) rethrow;
    }
  }

  Future<void> startSync() async {
    await refreshPending();
    sync.start(onFlushed: (int flushed) {
      refreshPending();
      if (_collectionsActive) refreshCollections();
    });
  }

  /// Handles anything the Share Sheet sends, on launch and afterwards.
  ///
  /// A share is just another way to press Save, so it goes through the same
  /// capture path -- including the offline path. That is the whole reason a
  /// capture cannot be lost without internet: sharing adds no new failure mode.
  Future<void> _captureShare(String payload) async {
    try {
      final outcome = await share.captureShared(payload, capture);
      if (outcome?.isQueued ?? false) await refreshPending();
      if (outcome != null) sharedCapture.value = outcome;
    } catch (error) {
      debugPrint('[services] share could not be saved: $error');
    }
  }

  Future<void> startShareHandling() async {
    _shareSubscription ??= share.shares.listen((payload) {
      _shareWork = (_shareWork ?? Future<void>.value()).then((_) => _captureShare(payload));
    });
    final initial = await share.readInitialShare();
    if (initial != null) _shareWork = (_shareWork ?? Future<void>.value()).then((_) => _captureShare(initial));
    await _shareWork;
  }

  Future<void>? _shareWork;

  StreamSubscription<String>? _shareSubscription;

  Future<void> stopAccountWork() async {
    if (_accountWorkStopped) return;
    _accountWorkStopped = true;
    await share.pauseDelivery();
    await _shareSubscription?.cancel();
    _shareSubscription = null;
    await sync.stop();
    await _collectionWork;
    await _shareWork;
  }

  Future<void> dispose() async {
    await stopAccountWork();
    await share.dispose();
    api.close();
    await db.close();
    pending.dispose();
    sharedCapture.dispose();
  }
}
