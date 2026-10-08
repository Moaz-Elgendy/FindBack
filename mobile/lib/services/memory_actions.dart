import 'dart:convert';

import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import 'guest_library.dart';
import 'items_service.dart';
import 'reminder_service.dart';
import 'sync_service.dart';

class MemoryDeletion {
  MemoryDeletion(
      {required this.id,
      required this.localOnly,
      required Future<void> Function() restore})
      : _restore = restore;
  final String id;
  final bool localOnly;
  final Future<void> Function() _restore;
  bool _restored = false;
  Future<void> undo() async {
    if (_restored) return;
    await _restore();
    _restored = true;
  }
}

/// Shared memory actions for the Library and detail screen.
class MemoryActions {
  MemoryActions(
      {required this.db,
      required this.api,
      required this.sync,
      this.guestLibrary,
      this.reminders,
      ConnectivityProbe? isOnline})
      : _isOnline = isOnline ?? systemIsOnline;
  final LocalDb db;
  final ApiClient api;
  final SyncService sync;
  final GuestLibrary? guestLibrary;
  final ReminderService? reminders;
  final ConnectivityProbe _isOnline;

  Future<String> _resolve(String id) async =>
      ItemsService.isLocalId(id) ? (await db.syncedItemId(id) ?? id) : id;

  Future<ItemDetail> edit(String id,
          {required String title, required String summary}) =>
      sync.exclusive(() async {
        id = await _resolve(id);
        if (ItemsService.isLocalId(id)) {
          throw ApiException(
              'This save must finish uploading before it can be edited.',
              kind: ApiFailureKind.rejected);
        }
        if (guestLibrary != null) {
          final item = await db.localItem(id);
          if (item == null) throw StateError('Memory is no longer available');
          final row = item.toLocalRow();
          final payload = Map<String, dynamic>.from(
              jsonDecode(row['brief_payload'] as String) as Map);
          row['title'] = title;
          row['title_clean'] = title;
          row['summary'] = summary;
          payload['instant_brief'] = summary;
          payload['edited'] = true;
          row['brief_payload'] = jsonEncode(payload);
          final edited = ItemDetail.fromLocalRow(row);
          await db.upsertRemoteItems([edited]);
          return edited;
        }
        final edited = await api.editItem(id, title: title, summary: summary);
        await db.upsertRemoteItems([edited]);
        return edited;
      });

  Future<void> retry(String id) async {
    await sync.exclusive(() async {
      id = await _resolve(id);
      if (ItemsService.isLocalId(id)) {
        final item = await db.localItem(id);
        if (item == null) throw StateError('Memory is no longer available');
        await db.queueSave(url: item.url);
        return;
      }
      final old = await db.localItem(id);
      if (guestLibrary != null && old?.edited == true) {
        throw ApiException('Use Summarize again to replace your edits.',
            kind: ApiFailureKind.rejected);
      }
      await _process(id, api.retryItem);
    });
    await sync.flush(force: true);
  }

  Future<void> keepLink(String id) => sync.exclusive(() async {
        id = await _resolve(id);
        if (ItemsService.isLocalId(id)) {
          throw ApiException('This save must finish uploading first.',
              kind: ApiFailureKind.rejected);
        }
        if (guestLibrary != null) {
          final old = await db.localItem(id);
          if (old == null) throw StateError('Memory is no longer available');
          final row = old.toLocalRow()..['status'] = 'link_only';
          final brief = jsonDecode(row['brief_payload'] as String)
              as Map<String, dynamic>;
          brief['link_only'] = true;
          row['brief_payload'] = jsonEncode(brief);
          await db.upsertRemoteItems([ItemDetail.fromLocalRow(row)]);
          return;
        }
        await db.upsertRemoteItems([await api.keepLinkOnly(id)]);
      });

  Future<ItemDetail> _process(
      String id, Future<ItemDetail> Function(String) remote, {bool reprocess = false}) async {
    ItemDetail item;
    if (guestLibrary != null) {
      final old = await db.localItem(id);
      if (old == null) throw StateError('Memory is no longer available');
      // Guest processing copies expire after the final Brief is cached.
      final fresh = await guestLibrary!.restartExpired(old, reprocess: reprocess);
      // Ingest already schedules a fresh Brief; do not enqueue it twice.
      if (fresh.isGeneratingBrief) return fresh;
      item = await remote(fresh.id);
      await guestLibrary!.cacheAndRelease(item);
    } else {
      item = await remote(id);
      await db.upsertRemoteItems([item]);
    }
    return item;
  }

  Future<ItemDetail> summarizeAgain(String id, {bool replaceEdits = false}) =>
      sync.exclusive(() async {
        id = await _resolve(id);
        if (ItemsService.isLocalId(id)) {
          throw ApiException('This save must finish uploading first.',
              kind: ApiFailureKind.rejected);
        }
        final old = await db.localItem(id);
        if (guestLibrary != null && old?.edited == true && !replaceEdits) {
          throw ApiException('Confirm replacing your edits first.',
              kind: ApiFailureKind.rejected);
        }
        return _process(
            id, (id) => api.summarizeAgain(id, replaceEdits: replaceEdits), reprocess: true);
      });

  Future<MemoryDeletion> delete(String id) => sync.exclusive(() async {
        id = await _resolve(id);
        final snapshot = await db.hideMemory(id);
        var remoteDeleted = false;
        var localOnly = false;
        try {
          await reminders?.suspend(id, true);
          if (!ItemsService.isLocalId(id) && guestLibrary == null) {
            if (!await _isOnline()) {
              localOnly = true;
            } else {
              try {
                await api.deleteUndoable(id);
                remoteDeleted = true;
              } on ApiException catch (error) {
                if (error.statusCode != 404) {
                  if (!error.isRetryableOffline) rethrow;
                  localOnly = true;
                }
              }
            }
          }
        } catch (_) {
          await db.restoreMemory(snapshot);
          await reminders?.suspend(id, false);
          rethrow;
        }
        final deletedId = id;
        return MemoryDeletion(
            id: id,
            localOnly: localOnly,
            restore: () => sync.exclusive(() async {
                  if (remoteDeleted) await api.restoreItem(deletedId);
                  await db.restoreMemory(snapshot);
                  await reminders?.suspend(deletedId, false);
                }));
      });
}
