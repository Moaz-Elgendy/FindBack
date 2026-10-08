import 'package:flutter/services.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/reminder.dart';
import 'package:findback/services/reminder_notifications.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  AndroidFlutterLocalNotificationsPlugin.registerWith();
  test('native immediate and scheduled notifications never include memory title', () async {
    final calls = <MethodCall>[];
    const channel = MethodChannel('dexterous.com/flutter/local_notifications');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
      calls.add(call);
      if (call.method == 'getNotificationAppLaunchDetails') {
        return {'notificationLaunchedApp':true,
          'notificationResponse':{'id':7,'notificationResponseType':0,'payload':'memory'}};
      }
      return call.method == 'initialize' ? true : null;
    });
    addTearDown(()=>TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel,null));
    final port = NativeReminderNotifications();
    String? opened;
    await port.initialize((id) => opened = id);
    expect(opened, 'memory');
    final item = ItemDetail.fromJson({'id':'memory','url':'https://example.test','title':'PRIVATE SECRET TITLE',
      'content_type':'recipe','created_at':'2026-10-01T12:00:00Z'});
    final value = MemoryReminder(itemId:item.id,scheduledAt:DateTime.now().toUtc().add(const Duration(days:1)),timeZone:'UTC',notificationId:7);
    await port.schedule(value,item);
    await port.show(value,item,DateTime.utc(2026,10,8));
    expect(calls.where((c)=>c.method == 'requestNotificationsPermission'),isEmpty);
    for(final call in calls.where((c)=>c.method == 'show' || c.method == 'zonedSchedule')) {
      final args = call.arguments as Map;
      expect(args['title'],'You asked to be reminded');
      expect(args['body'],startsWith('A recipe you saved'));
      expect(args['payload'],item.id);
      expect(args.toString(),isNot(contains('PRIVATE SECRET TITLE')));
      expect((args['platformSpecifics'] as Map)['visibility'],0);
    }
    expect(calls.where((c)=>c.method == 'show' || c.method == 'zonedSchedule').length,2);
  });
}
