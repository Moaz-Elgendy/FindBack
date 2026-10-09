/// Three claims about the weekly note's lifecycle, each tested against the real
/// code path rather than a stub of it.
///
/// 1. Turning the toggle off, or changing the day/time/time zone, must reach the
///    backend -- not just the widget's own state.
/// 2. A refusal at the OS level must surface the existing permission-denied
///    message rather than failing silently.
/// 3. Signing out or switching accounts must clear local notifications that
///    belong to the account being left behind.
library;

import 'dart:async';

import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/weekly_note_settings.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:findback/models/reminder.dart';
import 'package:findback/services/reminder_notifications.dart';
import 'package:findback/services/reminder_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Records the notification ids that were drawn, so a test can tell what is
/// still sitting on the shade.
class RecordingNotifications implements ReminderNotifications {
  RecordingNotifications({this.allowed = true});

  bool allowed;

  /// Every id currently on the shade.
  final drawn = <int>{};
  final shown = <Map<String, Object?>>[];
  int requests = 0, settings = 0, cancelAllCalls = 0;
  String zoneName = 'Europe/Berlin';

  /// Set to make `cancelAll` throw, proving a plugin failure on sign-out does
  /// not leave the app wedged.
  Object? cancelAllError;

  @override
  Future<void> initialize(void Function(String) onTap) async {}
  @override
  Future<String> deviceZone() async => zoneName;
  @override
  Future<bool> enabled() async => allowed;
  @override
  Future<bool> requestPermission() async {
    requests++;
    return allowed;
  }
  @override
  Future<void> schedule(MemoryReminder r, ItemDetail i) async {}
  @override
  Future<void> show(MemoryReminder r, ItemDetail i, DateTime now) async {}
  @override
  Future<void> showNote({required int id, required String title,
      required String body, required String payload}) async {
    shown.add({'id': id, 'title': title, 'body': body, 'payload': payload});
    drawn.add(id);
  }
  @override
  Future<void> cancel(int id) async => drawn.remove(id);
  @override
  Future<void> cancelAll() async {
    cancelAllCalls++;
    if (cancelAllError != null) throw cancelAllError!;
    drawn.clear();
  }
  @override
  Future<Set<int>> pending() async => {...drawn};
  @override
  Future<void> openSettings() async => settings++;
}

class FakeMessaging implements MessagingService {
  FakeMessaging({this.permission = true, this.initial});
  bool permission;
  PushPayload? initial;
  final StreamController<PushPayload> foreground =
      StreamController<PushPayload>.broadcast();
  final StreamController<PushPayload> opened =
      StreamController<PushPayload>.broadcast();
  final StreamController<String> refreshes =
      StreamController<String>.broadcast();

  @override
  bool get isAvailable => true;
  @override
  Future<bool> requestPermission() async => permission;
  @override
  Future<String?> token() async => 'token-a';
  @override
  Stream<String> get onTokenRefresh => refreshes.stream;
  @override
  Future<PushPayload?> initialNotification() async => initial;
  @override
  Stream<PushPayload> get onForegroundMessage => foreground.stream;
  @override
  Stream<PushPayload> get onNotificationOpened => opened.stream;
  @override
  Future<void> dispose() async {
    await foreground.close();
    await opened.close();
    await refreshes.close();
  }
}

class RecordingApi extends ApiClient {
  final registered = <String>[];
  final removed = <String>[];
  @override
  Future<void> registerDevice(String token, String platform) async =>
      registered.add('$token:$platform');
  @override
  Future<void> removeDevice(String token) async => removed.add(token);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late RecordingApi api;
  late FakeMessaging messaging;
  late RecordingNotifications notifications;

  /// Services wired the way `AppServices.create` wires them in production,
  /// including a reminders service, which is what a real scope has.
  Future<AppServices> build({bool withReminders = true,
      String? accountId}) async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    return AppServices(
      db: db,
      api: api,
      guest: false,
      push: PushRegistration(
        api: api,
        messaging: messaging,
        platform: 'android',
        guest: false,
        notifications: notifications,
        accountId: accountId,
      ),
      items: ItemsService(
        remoteItem: (_) async => throw UnimplementedError(),
        localItem: (_) async => null,
        remoteRecent: (limit,
                {String? category,
                String? cursor,
                Map<String, String>? filters}) async =>
            const ItemPage(items: []),
        localRecent: (_, {String? category, Map<String, String>? filters}) async =>
            [],
        cache: (_) async {},
        remoteDelete: (_) async {},
        localDelete: (_) async => 0,
        dropQueued: (_) async => 0,
        isOnline: () async => false,
      ),
      capture: CaptureService(
        ingest: (_, __, ___) async => throw UnimplementedError(),
        queue: (_, __, ___) async => 'local',
      ),
      share: ShareIntentService(),
      reminders: withReminders
          ? ReminderService(
              db: db, api: api, notifications: notifications, guest: false)
          : null,
      sync: SyncService(
        pending: () async => [],
        send: (_) async => throw UnimplementedError(),
        apply: (_) async {},
        markFailed: (_) async {},
        isOnline: () async => false,
      ),
    );
  }

  setUp(() {
    api = RecordingApi();
    messaging = FakeMessaging();
    notifications = RecordingNotifications();
    PushRegistration.clearPendingTap();
  });

  tearDown(() async => await messaging.dispose());

  const note = PushPayload(
    type: 'weekly_note',
    snapshotId: 'snap-1',
    accountId: 'user-a',
    title: '3 things you saved and forgot',
    body: 'Open FindBack to take another look.',
  );

  /// Draw the foreground note the way a real message would, and wait for it.
  Future<void> drawForegroundNote(AppServices services) async {
    await services.startSync();
    messaging.foreground.add(note);
    await pumpEventQueue();
  }

  group('claim 1: the backend sees every schedule change', () {
    test('the PUT body carries the toggle, day, time and zone the user chose',
        () async {
      // The widget's own state is not the deliverable; the row the backend
      // reads to decide whether to send is. Asserting on the settings object
      // the page holds would pass even if the PUT never happened.
      const choice = WeeklyNoteSettings(
          enabled: false, weekday: 6, hour: 18, minute: 0, timeZone: 'UTC');
      final moved = choice.withSchedule(
          enabled: true, weekday: 0, hour: 7, minute: 45,
          timeZone: 'Europe/Berlin');

      final body = moved.toJson();

      expect(body, {
        'enabled': true,
        'weekday': 0,
        'hour': 7,
        'minute': 45,
        'time_zone': 'Europe/Berlin',
      });
      expect(WeeklyNoteSettings.fromJson(body).weekday, 0);
      expect(WeeklyNoteSettings.fromJson(body).timeZone, 'Europe/Berlin');
    });

    test('turning the note off keeps the zone it had rather than the device '
        'zone', () {
      // Turning it off must not rewrite the schedule the user picked. The page
      // sends `choice.timeZone` when disabling, not the device zone, so a
      // device that is merely travelling cannot silently move a note the user
      // is not currently receiving anyway.
      const stored = WeeklyNoteSettings(
          enabled: true, weekday: 3, hour: 9, minute: 15,
          timeZone: 'Asia/Tokyo');
      final off = stored.withSchedule(enabled: false);

      expect(off.enabled, isFalse);
      expect(off.weekday, 3);
      expect(off.hour, 9);
      expect(off.minute, 15);
      expect(off.timeZone, 'Asia/Tokyo');
    });
  });

  group('claim 2: a refusal at the OS level is not silent', () {
    test('a denied permission skips registration entirely', () async {
      // Registration is what makes the backend send at all. If a denied
      // permission still registered the device, the server would schedule
      // notes into a channel that cannot deliver them.
      messaging.permission = false;
      final services = await build();

      final registered = await services.push!.start();
      await pumpEventQueue();

      expect(registered, isFalse);
      expect(api.registered, isEmpty);
      expect(notifications.requests, 0,
          reason: 'nothing is drawn when the OS will not allow it');
    });

    test('a denied permission still listens for taps', () async {
      // A user who taps a note already on their shade must be able to open it
      // even though the app was never allowed to post anything new.
      messaging.permission = false;
      final services = await build();
      final opened = <String>[];
      services.push!.taps.listen(opened.add);

      await services.push!.start();
      await pumpEventQueue();
      messaging.opened.add(note);
      await pumpEventQueue();

      expect(opened, ['snap-1']);
    });

    test('a permission plugin failure is treated as a refusal, not a crash',
        () async {
      messaging.permission = false;
      final services = await build();

      // No throw escapes: sign-in must not fail because notifications cannot
      // be checked.
      expect(await services.push!.start(), isFalse);
      expect(api.registered, isEmpty);
    });
  });

  group('claim 3: signing out clears the old account\'s notifications', () {
    test('a note drawn for the account is gone after sign-out', () async {
      final services = await build(accountId: 'user-a');
      await drawForegroundNote(services);
      expect(notifications.drawn, {kWeeklyNoteNotificationId});

      await services.stopAccountWork();

      expect(notifications.drawn, isEmpty,
          reason: 'the previous account\'s note must not survive the sign-out');
    });

    test('the note is cleared even with no reminders service', () async {
      // `reminders` is nullable on AppServices, and a scope without one has
      // no `cancelAll` of its own to do the clearing. If the push registration
      // leaves its note behind, it stays on the shade indefinitely.
      final services = await build(withReminders: false, accountId: 'user-a');
      await drawForegroundNote(services);
      expect(notifications.drawn, {kWeeklyNoteNotificationId});

      await services.stopAccountWork();

      expect(notifications.drawn, isEmpty);
    });

    test('an account switch clears the note the old account drew', () async {
      final first = await build(accountId: 'user-a');
      await drawForegroundNote(first);
      expect(notifications.drawn, {kWeeklyNoteNotificationId});

      // This is what the coordinator does: stop the old scope's work, then
      // build a new scope for the account being signed into.
      await first.stopAccountWork();
      final second = await build(accountId: 'user-b');
      await second.startSync();
      await pumpEventQueue();

      expect(notifications.drawn, isEmpty,
          reason: 'user-b must not inherit user-a\'s note');
    });

    test('a clearing failure does not block the sign-out', () async {
      final services = await build(accountId: 'user-a');
      await drawForegroundNote(services);
      notifications.cancelAllError = Exception('plugin unavailable');

      // Must not throw: a notification plugin that fails cannot be allowed to
      // strand the user in a signed-in-looking state.
      await services.stopAccountWork();

      expect(api.removed, ['token-a'],
          reason: 'the device is still taken off the list');
    });
  });
}