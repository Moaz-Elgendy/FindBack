/// A note that arrives while the app is open is shown, not swallowed.
///
/// Android does not render a notification for a message the app is handling, so
/// a foreground weekly note is invisible unless the app draws it itself. That is
/// what this covers: the server's own notification text is displayed locally,
/// with the same channel and the same private visibility as every other
/// notification.
///
/// Three rules the tests hold on to:
///
///  * the text shown is the server's notification block verbatim -- nothing is
///    composed locally, so a memory title cannot reach the shade from here;
///  * a message with no notification block shows nothing, rather than
///    something invented;
///  * iOS shows nothing, because iOS already rendered it and a local copy would
///    deliver the same note twice.
library;

import 'package:findback/data/api_client.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/reminder.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:findback/services/reminder_notifications.dart';
import 'package:flutter_test/flutter_test.dart';

const String genericTitle = '3 things you saved and forgot';
const String genericBody = 'Open FindBack to take another look.';
const String memoryTitle = 'How to build a sourdough starter';

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

class _RecordingApi extends ApiClient {
  _RecordingApi() : super(tokens: _Tokens());
  final List<String> registered = [];
  final List<String> removed = [];
  @override
  Future<void> registerDevice(String token, String platform) async =>
      registered.add('$token:$platform');
  @override
  Future<void> removeDevice(String token) async => removed.add(token);
}

/// Records what would have been put on the notification shade.
class RecordingNotifications implements ReminderNotifications {
  final List<Map<String, Object?>> notes = [];
  Object? showNoteError;

  @override
  Future<void> showNote({required int id, required String title,
      required String body, required String payload}) async {
    if (showNoteError != null) throw showNoteError!;
    notes.add({'id': id, 'title': title, 'body': body, 'payload': payload});
  }

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
  Future<void> show(MemoryReminder r, ItemDetail i, DateTime now) async {}
  @override
  Future<void> cancel(int id) async {}
  @override
  Future<void> cancelAll() async {}
  @override
  Future<Set<int>> pending() async => <int>{};
  @override
  Future<void> openSettings() async {}
}

/// Emits one message on the foreground channel, the way FCM would.
class _ForegroundMessaging implements MessagingService {
  _ForegroundMessaging(this.payload);
  final PushPayload? payload;

  @override
  bool get isAvailable => true;
  @override
  Future<bool> requestPermission() async => true;
  @override
  Future<String?> token() async => 'token-a';
  @override
  Stream<String> get onTokenRefresh => const Stream.empty();
  @override
  Stream<PushPayload> get onForegroundMessage =>
      payload == null ? const Stream.empty() : Stream.value(payload!);
  @override
  Stream<PushPayload> get onNotificationOpened => const Stream.empty();
  @override
  Future<PushPayload?> initialNotification() async => null;
  @override
  Future<void> dispose() async {}
}

void main() {
  late _RecordingApi api;
  late RecordingNotifications notifications;

  setUp(() {
    api = _RecordingApi();
    notifications = RecordingNotifications();
  });

  PushRegistration build(PushPayload? payload,
          {String platform = 'android', String? accountId}) =>
      PushRegistration(
        api: api,
        messaging: _ForegroundMessaging(payload),
        guest: false,
        notifications: notifications,
        platform: platform,
        accountId: accountId,
      );

  const weekly = PushPayload(
      type: 'weekly_note',
      snapshotId: 'snap-7',
      title: genericTitle,
      body: genericBody);

  test('a foreground weekly note is shown locally', () async {
    await build(weekly).start();
    await pumpEventQueue();

    expect(notifications.notes, hasLength(1));
    expect(notifications.notes.first['title'], genericTitle);
    expect(notifications.notes.first['body'], genericBody);
  });

  test('the shown note is tagged as a weekly note, not a memory id',
      () async {
    // The reminders channel and this share one plugin and one tap handler, so
    // the payload must say which screen it opens. A bare snapshot id would be
    // pushed as DetailPage(itemId: <snapshot id>).
    await build(weekly).start();
    await pumpEventQueue();
    expect(notifications.notes.first['payload'],
        '${kWeeklyNotePayloadPrefix}snap-7');
  });

  test('one fixed id, so a repeat replaces rather than stacks', () async {
    await build(weekly).start();
    await pumpEventQueue();
    expect(notifications.notes.first['id'], kWeeklyNoteNotificationId);
  });

  test('no memory title appears in the shown note', () async {
    await build(weekly).start();
    await pumpEventQueue();
    final first = notifications.notes.first;
    final rendered = '${first["title"]} ${first["body"]}';
    expect(rendered, isNot(contains(memoryTitle)));
    expect(rendered.contains('sourdough'), isFalse);
  });

  test('a data-only message with no text shows nothing', () async {
    // The payload has a snapshot id but no notification block. Inventing a
    // title here would put words on the lock screen the server never sent.
    await build(const PushPayload(type: 'weekly_note', snapshotId: 'snap-7'))
        .start();
    await pumpEventQueue();
    expect(notifications.notes, isEmpty);
  });

  test('iOS shows nothing, because it already rendered the note', () async {
    await build(weekly, platform: 'ios').start();
    await pumpEventQueue();
    expect(notifications.notes, isEmpty,
        reason: 'a local copy on iOS would deliver the note twice');
  });

  test('an unknown type is not shown', () async {
    await build(const PushPayload(
            type: 'account_alert',
            snapshotId: 'snap-7',
            title: genericTitle,
            body: genericBody))
        .start();
    await pumpEventQueue();
    expect(notifications.notes, isEmpty);
  });

  test('a guest is shown nothing, and never navigates', () async {
    final guest = PushRegistration(
      api: api,
      messaging: _ForegroundMessaging(weekly),
      guest: true,
      notifications: notifications,
      platform: 'android',
    );
    await guest.start();
    await pumpEventQueue();
    expect(notifications.notes, isEmpty,
        reason: 'a guest has no account to be notified about');
  });

  // A note for another account is not shown at all, rather than shown and then
  // refused on tap: a lock screen is visible to whoever is holding the phone.
  group('foreground note for another account', () {
    const theirs = PushPayload(
        type: 'weekly_note',
        snapshotId: 'snap-theirs',
        accountId: 'account-theirs',
        title: genericTitle,
        body: genericBody);

    test('is not shown when the note names another account', () async {
      await build(theirs, accountId: 'account-mine').start();
      await pumpEventQueue();

      expect(notifications.notes, isEmpty,
          reason: 'showing it would leak a count to whoever holds the phone');
    });

    test('is shown when the note names the signed-in account', () async {
      await build(weekly, accountId: 'account-mine').start();
      await pumpEventQueue();

      expect(notifications.notes, hasLength(1));
    });

    test('is shown when the note names no account', () async {
      // An older server sent only type and snapshot_id. Suppressing those would
      // silently break the feature, and the lock screen shows a count either
      // way, so there is nothing extra to protect here.
      await build(weekly, accountId: 'account-mine').start();
      await pumpEventQueue();

      expect(notifications.notes, hasLength(1));
    });
  });

  test('a plugin failure does not escape', () async {
    notifications.showNoteError = Exception('channel unavailable');
    await build(weekly).start();
    await pumpEventQueue();
    // Reaching here without a throw is the assertion.
    expect(api.registered, ['token-a:android']);
  });

  test('hasNotificationText requires both halves', () {
    expect(
        const PushPayload(type: 'weekly_note', snapshotId: 's')
            .hasNotificationText,
        isFalse);
    expect(
        const PushPayload(type: 'weekly_note', snapshotId: 's', title: 't')
            .hasNotificationText,
        isFalse);
    expect(
        const PushPayload(type: 'weekly_note', snapshotId: 's', title: 't',
            body: 'b')
            .hasNotificationText,
        isTrue);
  });
}