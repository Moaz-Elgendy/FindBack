import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/reminder.dart';
import 'package:findback/services/reminder_service.dart';
import 'package:findback/services/reminder_notifications.dart';

class FakeNotifications extends ReminderNotifications {
  bool allowed = false;
  String zoneName = 'UTC';
  int requests = 0, shown = 0, settings = 0;
  final scheduled = <int>{};
  final alarms = <int, DateTime>{};
  void Function(String)? onTap;
  @override Future<void> initialize(void Function(String) onTap) async { this.onTap = onTap; }
  @override Future<String> deviceZone() async => zoneName;
  @override Future<bool> enabled() async => allowed;
  @override Future<bool> requestPermission() async { requests++; return allowed; }
  @override Future<void> schedule(MemoryReminder r, ItemDetail i) async { scheduled.add(r.notificationId); alarms[r.notificationId] = r.scheduledAt; }
  @override Future<void> show(MemoryReminder r, ItemDetail i, DateTime now) async { shown++; }
  @override Future<void> showNote({required int id, required String title, required String body, required String payload}) async {}
  @override Future<void> cancel(int id) async { scheduled.remove(id); alarms.remove(id); }
  @override Future<void> cancelAll() async { scheduled.clear(); alarms.clear(); }
  @override Future<Set<int>> pending() async => {...scheduled};
  @override Future<void> openSettings() async { settings++; }
}

class ReminderApi extends ApiClient {
  MemoryReminder? remote;
  bool offline = false;
  void checkOnline() { if (offline) throw ApiException('offline'); }
  @override Future<List<MemoryReminder>> listReminders() async { checkOnline(); return [if (remote != null) remote!]; }
  @override Future<void> setReminder(MemoryReminder value) async { checkOnline(); remote = value; }
  @override Future<void> acknowledgeReminder(MemoryReminder value) async { checkOnline(); if (remote?.scheduledAt == value.scheduledAt) remote = null; }
  @override Future<void> removeReminder(String id) async { checkOnline(); remote = null; }
}

void main() {
  sqfliteFfiInit();
  databaseFactory = databaseFactoryFfi;
  late LocalDb db;
  late ReminderService service;
  late FakeNotifications notifications;
  late DateTime now;
  final item = ItemDetail.fromJson({'id':'memory', 'url':'https://example.test', 'status':'ready',
    'title':'PRIVATE MEMORY TITLE', 'content_type':'video', 'created_at':'2026-10-01T12:00:00Z'});
  setUp(() async {
    now = DateTime.utc(2026,10,8,12);
    db = await LocalDb.openAt(inMemoryDatabasePath);
    await db.upsertRemoteItems([item]);
    notifications = FakeNotifications();
    service = ReminderService(db:db, api:ApiClient(), notifications:notifications, guest:true, clock:()=>now);
  });
  tearDown(() async { await service.stop(); service.dispose(); service.api.close(); await db.close(); });
  test('denied permission stores choice; later permission schedules without requesting at launch', () async {
    await service.start();
    expect(notifications.requests, 0);
    expect(await service.set(item, now.add(const Duration(hours:2)), 'UTC', explainPermission:() async=>true), false);
    expect((await service.current(item.id))!.timeZone, 'UTC');
    notifications.allowed = true;
    await service.reconcile();
    expect(notifications.scheduled.length, 1);
    expect(notifications.requests, 1);
  });
  test('more than 24h late and previously denied still delivers; never repeats', () async {
    await service.set(item, now.add(const Duration(hours:2)), 'UTC', explainPermission:() async=>true);
    now = now.add(const Duration(days:3));
    notifications.allowed = true;
    await service.reconcile();
    expect(notifications.shown, 1);
    await service.reconcile();
    expect(notifications.shown, 1);
    expect(await service.current(item.id), isNull);
  });
  test('replacement, deletion and Undo leave one alarm; remove cancels it', () async {
    notifications.allowed = true;
    await service.set(item, now.add(const Duration(hours:2)), 'UTC', explainPermission:() async=>true);
    await service.set(item, now.add(const Duration(hours:3)), 'UTC', explainPermission:() async=>true);
    expect(notifications.scheduled.length, 1);
    await service.suspend(item.id, true);
    expect(notifications.scheduled, isEmpty);
    await service.suspend(item.id, false);
    expect(notifications.scheduled.length, 1);
    await service.remove(item.id);
    expect(notifications.scheduled, isEmpty);
    expect(await service.current(item.id), isNull);
  });
  test('a remote replacement updates an already pending native alarm', () async {
    await service.stop(); service.dispose(); service.api.close();
    final api = ReminderApi()..remote = MemoryReminder(itemId:item.id, scheduledAt:now.add(const Duration(hours:2)),timeZone:'UTC');
    notifications.allowed = true;
    service = ReminderService(db:db,api:api,notifications:notifications,clock:()=>now);
    await service.reconcile();
    expect(notifications.alarms.values.single,api.remote!.scheduledAt);
    api.remote = MemoryReminder(itemId:item.id,scheduledAt:now.add(const Duration(hours:3)),timeZone:'UTC');
    await service.reconcile();
    expect(notifications.alarms.length,1);
    expect(notifications.alarms.values.single,api.remote!.scheduledAt);
  });
  test('an offline replacement that fires cannot resurrect the previous remote alarm', () async {
    await service.stop(); service.dispose(); service.api.close();
    final api=ReminderApi()..remote=MemoryReminder(itemId:item.id,scheduledAt:now.add(const Duration(hours:5)),timeZone:'UTC');
    notifications.allowed=true;
    service=ReminderService(db:db,api:api,notifications:notifications,clock:()=>now);
    await service.reconcile(); api.offline=true;
    await service.set(item,now.add(const Duration(hours:2)),'UTC',explainPermission:() async=>true);
    now=now.add(const Duration(hours:3)); await service.reconcile();
    expect(notifications.shown,1); expect(api.remote,isNotNull);
    api.offline=false; await service.reconcile();
    expect(api.remote,isNull); expect(await service.current(item.id),isNull);
    expect(notifications.scheduled,isEmpty);
    await service.reconcile(); expect(notifications.shown,1);
  });
  test('guest reminder survives sign-in import with the queued item id', () async {
    final directory=await Directory.systemTemp.createTemp('fb-guest-reminder-');
    final guest=await LocalDb.openAt('${directory.path}/guest.db');
    addTearDown(() async { await guest.close(); await directory.delete(recursive:true); });
    await guest.upsertRemoteItems([item]);
    await guest.db.insert('reminders',{'item_id':item.id,'scheduled_at':now.add(const Duration(hours:2)).toIso8601String(),'time_zone':'UTC'});
    await db.db.delete('items'); await db.importGuest(guest);
    final memory=(await db.db.query('items')).single;
    final reminder=(await db.db.query('reminders')).single;
    expect(reminder['item_id'],memory['id']);
    expect((reminder['item_id'] as String).startsWith('local-'),true);
    expect(reminder['dirty'],'set');
    expect((await guest.db.query('reminders')).single['item_id'],item.id);
  });
  test('notification body contains only type and age', () {
    expect(reminderBody(item, now), 'A video you saved last week');
    expect(reminderBody(item, now), isNot(contains(item.bestTitle)));
    expect(NativeReminderNotifications.details.android!.visibility?.name, 'private');
  });
}
