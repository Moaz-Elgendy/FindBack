import 'dart:async';
import 'package:flutter/foundation.dart';
import '../data/api_client.dart';
import '../data/local_db.dart';
import '../models/item.dart';
import '../models/reminder.dart';
import 'reminder_notifications.dart';

class ReminderService extends ChangeNotifier {
  ReminderService({required this.db, required this.api, required this.notifications,
    this.guest = false, DateTime Function()? clock}) : clock = clock ?? DateTime.now;
  final LocalDb db;
  final ApiClient api;
  final ReminderNotifications notifications;
  final bool guest;
  final DateTime Function() clock;
  Future<void>? _work;
  Future<void> _tail = Future.value();
  int _serialCount = 0;
  bool _stopped = false;
  final _taps = StreamController<String>.broadcast();
  Stream<String> get taps => _taps.stream;
  String? initialTap;

  Future<void> start() async {
    if (_stopped) return;
    await notifications.initialize((id) {
      if (_stopped) return;
      if (_taps.hasListener) { _taps.add(id); }
      else { initialTap = id; }
    });
    await reconcile();
  }

  Future<MemoryReminder?> current(String id) async {
    final rows = await db.db.query('reminders', where: 'item_id=? AND dirty!=? AND suspended=0 AND delivered=0', whereArgs: [id, 'remove']);
    return rows.isEmpty ? null : MemoryReminder.fromJson(Map<String, dynamic>.from(rows.single));
  }

  Future<List<MemoryReminder>> _all() async => (await db.db.query('reminders'))
    .map((row) => MemoryReminder.fromJson(Map<String, dynamic>.from(row))).toList();

  Future<MemoryReminder> _store(String id, DateTime at, String zone, {String dirty = ''}) async {
    await db.db.rawInsert('''INSERT INTO reminders(item_id,scheduled_at,time_zone,dirty) VALUES(?,?,?,?)
      ON CONFLICT(item_id) DO UPDATE SET scheduled_at=excluded.scheduled_at,
      time_zone=excluded.time_zone,delivered=0,scheduled=0,dirty=excluded.dirty''', [id, at.toUtc().toIso8601String(), zone, dirty]);
    final rows = await db.db.query('reminders', where: 'item_id=?', whereArgs: [id]);
    return MemoryReminder.fromJson(Map<String, dynamic>.from(rows.single));
  }

  Future<bool> set(ItemDetail item, DateTime at, String zone,
      {required Future<bool> Function() explainPermission}) => _serial(() async {
    if (!at.isAfter(clock())) throw ArgumentError('Choose a future time');
    // Store before permissions: denial must never discard the user's choice.
    final reminder = await _store(item.id, at, zone, dirty: guest || item.id.startsWith('local-') ? '' : 'set');
    await notifications.cancel(reminder.notificationId);
    notifyListeners();
    var allowed = await notifications.enabled();
    if (!allowed && await explainPermission()) allowed = await notifications.requestPermission();
    if (allowed) await _schedule(reminder, item);
    await _reconcile();
    return allowed;
  });

  Future<void> remove(String id) => _serial(() async {
    final value = await current(id);
    if (value == null) return;
    await notifications.cancel(value.notificationId);
    if (guest || id.startsWith('local-')) {
      await db.db.delete('reminders', where: 'item_id=?', whereArgs: [id]);
    } else {
      await db.db.update('reminders', {'dirty': 'remove'}, where: 'item_id=?', whereArgs: [id]);
    }
    notifyListeners();
    await _reconcile();
  });

  Future<void> suspend(String id, bool hidden) => _serial(() async {
    final rows = await db.db.query('reminders', where: 'item_id=?', whereArgs: [id]);
    if (rows.isEmpty) return;
    await db.db.update('reminders', {'suspended': hidden ? 1 : 0, if (hidden) 'scheduled': 0}, where: 'item_id=?', whereArgs: [id]);
    if (hidden) { await notifications.cancel(rows.single['notification_id'] as int); }
    else { await _reconcile(); }
  });

  Future<void> reconcile() {
    if (_stopped) return Future.value();
    return _work ??= _serial(_reconcile).whenComplete(() => _work = null);
  }

  Future<T> _serial<T>(Future<T> Function() action) {
    _serialCount++;
    final next = _tail.then((_) => action()).whenComplete(() => _serialCount--);
    _tail = next.then<void>((_) {}, onError: (Object _, StackTrace __) {});
    return next;
  }

  Future<void> _reconcile() async {
    if (_stopped) return;
    if (!guest) {
      for (final value in await _all()) {
        if (!value.itemId.startsWith('local-')) continue;
        final mapped = await db.syncedItemId(value.itemId);
        if (mapped == null) continue;
        await db.db.update('reminders', {'item_id': mapped, 'dirty': value.delivered ? 'delivered' : 'set'},
          where: 'item_id=?', whereArgs: [value.itemId]);
      }
      try {
        for (final value in await _all()) {
          if (_stopped) return;
          if (value.suspended || value.itemId.startsWith('local-')) continue;
          if (value.dirty == 'remove') {
            await api.removeReminder(value.itemId);
            if (!value.delivered) await notifications.cancel(value.notificationId);
            await db.db.delete('reminders', where: 'item_id=? AND dirty=?', whereArgs: [value.itemId, 'remove']);
          } else if (value.dirty == 'set' && value.scheduledAt.isAfter(clock())) {
            await api.setReminder(value);
            await db.db.update('reminders', {'dirty': ''}, where: 'item_id=? AND scheduled_at=?', whereArgs: [value.itemId, value.scheduledAt.toIso8601String()]);
          } else if (value.dirty == 'delivered') {
            try { await api.acknowledgeReminder(value); }
            on ApiException catch (error) { if (error.statusCode != 404) rethrow; }
            await db.db.update('reminders', {'dirty': ''}, where: 'item_id=? AND scheduled_at=?', whereArgs: [value.itemId, value.scheduledAt.toIso8601String()]);
          }
        }
        final remote = await api.listReminders();
        final local = await _all();
        for (final value in remote) {
          final previous = local.where((r) => r.itemId == value.itemId).firstOrNull;
          if (previous?.dirty.isNotEmpty == true || previous?.suspended == true) continue;
          if (previous == null || previous.scheduledAt != value.scheduledAt) {
            await _store(value.itemId, value.scheduledAt, value.timeZone);
          }
        }
        for (final value in local) {
          if (value.dirty.isEmpty && !value.suspended && !value.itemId.startsWith('local-') &&
              !remote.any((r) => r.itemId == value.itemId)) {
            await notifications.cancel(value.notificationId);
            await db.db.delete('reminders', where: 'item_id=? AND dirty=?', whereArgs: [value.itemId, '']);
          }
        }
      } on ApiException catch (error) {
        if (!error.isRetryableOffline) rethrow;
      }
    }
    if (_stopped || !await notifications.enabled()) return;
    final pending = await notifications.pending();
    for (final value in await _all()) {
      if (_stopped) return;
      if (value.suspended || value.delivered || value.dirty == 'remove' || db.isHiddenId(value.itemId)) continue;
      var item = await db.localItem(value.itemId);
      if (item == null && !guest && !value.itemId.startsWith('local-')) {
        try { item = await api.getItem(value.itemId); await db.upsertRemoteItems([item]); }
        on ApiException catch (error) {
          if (error.statusCode == 404) {
            await notifications.cancel(value.notificationId);
            await db.db.delete('reminders', where: 'item_id=?', whereArgs: [value.itemId]);
          } else if (!error.isRetryableOffline) { rethrow; }
        }
      }
      if (item == null) continue;
      if (value.scheduledAt.isAfter(clock())) {
        if (!pending.contains(value.notificationId) || !value.scheduled) await _schedule(value, item);
      } else {
        // Pending past alarms include reboot/offline delays; use the same id.
        // Replacing that notification prevents duplicate entries in the tray.
        if (pending.contains(value.notificationId) || !value.scheduled) {
          await notifications.cancel(value.notificationId);
          await notifications.show(value, item, clock());
        }
        // An unsynced replacement that already fired must clear the old server alarm.
        await db.db.update('reminders', {'delivered': 1, 'dirty': guest || value.itemId.startsWith('local-') ? '' : value.dirty == 'set' ? 'remove' : 'delivered'},
          where: 'item_id=? AND scheduled_at=?', whereArgs: [value.itemId, value.scheduledAt.toIso8601String()]);
      }
    }
    notifyListeners();
  }

  Future<void> _schedule(MemoryReminder value, ItemDetail item) async {
    await notifications.schedule(value, item);
    await db.db.update('reminders', {'scheduled': 1}, where: 'item_id=? AND scheduled_at=?',
      whereArgs: [value.itemId, value.scheduledAt.toIso8601String()]);
  }

  Future<void> stop() async {
    _stopped = true;
    if (_serialCount > 0) await _tail;
    await notifications.cancelAll();
  }
  @override
  void dispose() { _taps.close(); super.dispose(); }
}
