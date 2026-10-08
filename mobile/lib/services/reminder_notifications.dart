import 'dart:io';
import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest.dart' as data;
import 'package:timezone/timezone.dart' as tz;
import '../models/item.dart';
import '../models/reminder.dart';
import 'reminder_slots.dart';

String reminderBody(ItemDetail item, DateTime delivery) {
  final type = switch (item.contentType) { 'video' => 'video', 'recipe' => 'recipe', 'product' => 'product', _ => 'link' };
  final days = item.createdAt == null ? null : delivery.toUtc().difference(item.createdAt!.toUtc()).inDays;
  final age = days == null ? '' : days <= 0 ? ' today' : days == 1 ? ' yesterday' : days < 7 ? ' $days days ago' : days < 14 ? ' last week' : ' ${(days / 7).floor()} weeks ago';
  return 'A $type you saved$age';
}

abstract class ReminderNotifications {
  Future<String> deviceZone();
  Future<bool> enabled();
  Future<bool> requestPermission();
  Future<void> initialize(void Function(String) onTap);
  Future<void> schedule(MemoryReminder reminder, ItemDetail item);
  Future<void> show(MemoryReminder reminder, ItemDetail item, DateTime now);
  Future<void> cancel(int id);
  Future<void> cancelAll();
  Future<Set<int>> pending();
  Future<void> openSettings();
}

class NativeReminderNotifications implements ReminderNotifications {
  final _plugin = FlutterLocalNotificationsPlugin();
  static const _settingsChannel = MethodChannel('findback/notifications');
  static const details = NotificationDetails(
    android: AndroidNotificationDetails('memory_reminders', 'Memory reminders',
      channelDescription: 'Reminders you asked for', importance: Importance.high,
      priority: Priority.high, visibility: NotificationVisibility.private),
    iOS: DarwinNotificationDetails());
  bool _ready = false;

  @override
  Future<String> deviceZone() async {
    data.initializeTimeZones();
    return (await FlutterTimezone.getLocalTimezone()).identifier;
  }
  @override
  Future<void> initialize(void Function(String) onTap) async {
    if (_ready) return;
    data.initializeTimeZones();
    await _plugin.initialize(settings: const InitializationSettings(
      android: AndroidInitializationSettings('@drawable/ic_notification'),
      iOS: DarwinInitializationSettings(requestAlertPermission: false,
        requestBadgePermission: false, requestSoundPermission: false)),
      onDidReceiveNotificationResponse: (response) {
        if (response.payload != null) onTap(response.payload!);
      });
    _ready = true;
    final launch = await _plugin.getNotificationAppLaunchDetails();
    final payload = launch?.notificationResponse?.payload;
    if (launch?.didNotificationLaunchApp == true && payload != null) onTap(payload);
  }
  @override
  Future<bool> enabled() async {
    if (Platform.isAndroid) return await _plugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()?.areNotificationsEnabled() ?? false;
    if (Platform.isIOS) return (await _plugin.resolvePlatformSpecificImplementation<IOSFlutterLocalNotificationsPlugin>()?.checkPermissions())?.isEnabled ?? false;
    return false;
  }
  @override
  Future<bool> requestPermission() async {
    if (Platform.isAndroid) return await _plugin.resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()?.requestNotificationsPermission() ?? false;
    if (Platform.isIOS) return await _plugin.resolvePlatformSpecificImplementation<IOSFlutterLocalNotificationsPlugin>()?.requestPermissions(alert: true, badge: true, sound: true) ?? false;
    return false;
  }
  @override
  Future<void> schedule(MemoryReminder reminder, ItemDetail item) => _plugin.zonedSchedule(
    id: reminder.notificationId, title: 'You asked to be reminded',
    body: reminderBody(item, reminder.scheduledAt), payload: reminder.itemId,
    scheduledDate: tz.TZDateTime.from(reminder.scheduledAt, reminderZone(reminder.timeZone)),
    notificationDetails: details, androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle);
  @override
  Future<void> show(MemoryReminder reminder, ItemDetail item, DateTime now) => _plugin.show(
    id: reminder.notificationId, title: 'You asked to be reminded',
    body: reminderBody(item, now), payload: reminder.itemId, notificationDetails: details);
  @override
  Future<void> cancel(int id) => _plugin.cancel(id: id);
  @override
  Future<void> cancelAll() => _plugin.cancelAll();
  @override
  Future<Set<int>> pending() async => (await _plugin.pendingNotificationRequests()).map((r) => r.id).toSet();
  @override
  Future<void> openSettings() => _settingsChannel.invokeMethod<void>('openSettings');
}
