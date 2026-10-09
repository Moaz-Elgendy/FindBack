/// Permission, resume, and what happens to the previous account's state on
/// the way out.
///
/// Three behaviours meet here, all of them about the same token and the same
/// scheduled reminders:
///
///   * a denied permission is reported and remembered, not retried in a loop;
///   * granting permission in system settings is noticed on the next resume,
///     because the app cannot see the moment it changes;
///   * signing out or switching account takes the token AND the previous
///     account's scheduled reminders with it, so the next person to use the
///     phone inherits neither.
library;


import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/reminder.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:findback/services/reminder_notifications.dart';
import 'package:findback/services/reminder_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

import 'push_notification_tap_test.dart' show FakeMessaging;

/// Records every call and decides what each returns.
class _RecordingApi extends ApiClient {
  _RecordingApi() : super(tokens: _Tokens());
  final List<String> registered = [];
  final List<String> removed = [];
  Object? removeError;

  @override
  Future<void> registerDevice(String token, String platform) async =>
      registered.add('$token:$platform');

  @override
  Future<void> removeDevice(String token) async {
    if (removeError != null) throw removeError!;
    removed.add(token);
  }
}

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

/// Counts the `cancelAll` that wipes scheduled reminders for an account.
class FakeReminderNotifications implements ReminderNotifications {
  int cancelAllCalls = 0;
  @override
  Future<void> cancelAll() async => cancelAllCalls++;
  @override
  Future<String> deviceZone() async => 'UTC';
  @override
  Future<bool> enabled() async => true;
  @override
  Future<bool> requestPermission() async => true;
  @override
  Future<void> initialize(void Function(String) onTap) async {}
  @override
  Future<void> schedule(MemoryReminder reminder, ItemDetail item) async {}
  @override
  Future<void> show(MemoryReminder reminder, ItemDetail item, DateTime now) async {}
  @override
  Future<void> cancel(int id) async {}
  @override
  Future<Set<int>> pending() async => <int>{};
  @override
  Future<void> openSettings() async {}

  /// Every locally rendered note, so a test can assert what reached the shade.
  final List<Map<String, Object?>> notes = [];
  Object? showNoteError;

  @override
  Future<void> showNote({required int id, required String title,
      required String body, required String payload}) async {
    if (showNoteError != null) throw showNoteError!;
    notes.add({'id': id, 'title': title, 'body': body, 'payload': payload});
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late _RecordingApi api;
  late FakeMessaging messaging;
  late FakeReminderNotifications reminders;
  late AppServices services;

  Future<AppServices> build({bool guest = false}) async {
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    return AppServices(
      db: db,
      api: api,
      guest: guest,
      push: PushRegistration(
          api: api, messaging: messaging, platform: 'android', guest: guest),
      items: ItemsService(
        remoteItem: (_) async => throw UnimplementedError(),
        localItem: (_) async => null,
        remoteRecent: (limit,
                {String? category, String? cursor, Map<String, String>? filters}) async =>
            const ItemPage(items: []),
        localRecent: (_, {String? category, Map<String, String>? filters}) async => [],
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
      reminders: ReminderService(
          db: db, api: api, notifications: reminders, guest: guest),
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
    api = _RecordingApi();
    messaging = FakeMessaging();
    reminders = FakeReminderNotifications();
    PushRegistration.clearPendingTap();
  });

  tearDown(() async => await messaging.dispose());

  /// Builds services and starts them, the way the app does at sign-in.
  Future<void> signedIn({bool guest = false}) async {
    services = await build(guest: guest);
    await services.startSync();
  }

  // --- 2. permission denied, then granted in system settings ---------------

  group('permission denied and later granted', () {
    test('a denied permission registers nothing', () async {
      messaging.permission = false;
      await signedIn();
      expect(api.registered, isEmpty);
      expect(services.push!.registeredToken, isNull);
    });

    test('nothing is registered on resume either while still denied',
        () async {
      messaging.permission = false;
      await signedIn();
      await services.push!.start();
      expect(api.registered, isEmpty,
          reason: 'a repeated attempt must not nag or register');
    });

    test('registering on resume after the user grants permission', () async {
      // The app cannot observe the moment permission changes, so the only
      // place it can notice is the next resume.
      messaging.permission = false;
      await signedIn();
      expect(api.registered, isEmpty);

      messaging.permission = true;
      await services.push!.start(); // what _resumePush calls
      expect(api.registered, ['token-a:android']);
      expect(services.push!.registeredToken, 'token-a');
    });

    test('resume registers once per resume, not repeatedly', () async {
      await signedIn();
      expect(api.registered.length, 1);
      await services.push!.start();
      // Safe because the backend upserts on the token; the app is expected to
      // check on every resume because permission can change between them.
      expect(api.registered.length, 2);
      expect(api.registered.toSet().length, 1,
          reason: 'and it is always the same token');
    });
  });

  // --- 3. signing out / switching account ---------------------------------

  group('signing out', () {
    test('removes the device token', () async {
      await signedIn();
      await services.stopAccountWork();
      expect(api.removed, ['token-a']);
    });

    test('cancels the previous account\'s scheduled reminders', () async {
      await signedIn();
      await services.stopAccountWork();
      expect(reminders.cancelAllCalls, 1,
          reason: 'a reminder due later must not fire for whoever signs in next');
    });

    test('a failed removal still cancels the reminders', () async {
      await signedIn();
      api.removeError = Exception('offline');
      await services.stopAccountWork();
      expect(reminders.cancelAllCalls, 1,
          reason: 'a sign-out must not be blocked by a failed DELETE');
    });

    test('an account switch re-registers for the new account', () async {
      await signedIn();
      await services.stopAccountWork();
      expect(api.removed, ['token-a']);

      // The next scope's services register the same physical device again.
      final next = await build();
      await next.startSync();
      expect(api.registered, ['token-a:android', 'token-a:android']);
      await next.dispose();
    });

    test('stopping twice does not delete the token twice', () async {
      await signedIn();
      await services.stopAccountWork();
      await services.stopAccountWork();
      expect(api.removed, ['token-a']);
    });
  });

  // --- 4. account deletion cascades ---------------------------------------

  group('account deletion', () {
    test('the backend removes the token with the account row', () async {
      // The cascade itself is a database guarantee, proved on the backend in
      // tests/test_devices.py (`test_the_cascade_is_the_database_not_the_route`).
      // What is checkable here is that the app unregisters first, so the token
      // is not left behind if the delete request itself fails.
      await signedIn();
      await services.stopAccountWork();
      expect(api.removed, ['token-a']);
    });
  });
}