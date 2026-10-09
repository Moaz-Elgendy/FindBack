import 'dart:async';

import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:flutter_test/flutter_test.dart';

import 'push_registration_test.dart' show FakeMessaging;
import 'push_token_refresh_test.dart' show CountingMessaging;
import 'weekly_note_lifecycle_test.dart' show RecordingApi, RecordingNotifications;
import 'package:findback/data/api_client.dart';

class LazyMessaging extends FakeMessaging {
  bool ready = false;

  @override
  Future<PushPayload?> initialNotification() async {
    ready = true;
    return super.initialNotification();
  }

  @override
  Stream<PushPayload> get onNotificationOpened =>
      ready ? super.onNotificationOpened : const Stream.empty();

  @override
  Stream<PushPayload> get onForegroundMessage =>
      ready ? super.onForegroundMessage : const Stream.empty();
}

class RegistrationApi extends ApiClient {
  final started = Completer<void>();
  final finish = Completer<void>();
  final removed = <String>[];

  @override
  Future<void> registerDevice(String token, String platform) async {
    started.complete();
    await finish.future;
  }

  @override
  Future<void> removeDevice(String token) async => removed.add(token);
}

void main() {
  test('startup does not prompt when notification permission is absent', () async {
    final messaging = FakeMessaging();
    final api = RecordingApi();
    final push = PushRegistration(api: api, messaging: messaging,
        notifications: RecordingNotifications(allowed: false),
        platform: 'android', guest: false);
    expect(await push.start(), isFalse);
    expect(messaging.calls, isNot(contains('requestPermission')));
    expect(api.registered, isEmpty);
    await push.dispose();
    await messaging.dispose();
    api.close();
  });

  test('notification streams initialize before subscribing, including guests', () async {
    final messaging = LazyMessaging();
    final api = ApiClient();
    final push = PushRegistration(api: api, messaging: messaging,
        platform: 'android', guest: true);
    PushRegistration.clearPendingTap();
    await push.start();
    await messaging.openFromShade(const PushPayload(
        type: 'weekly_note', snapshotId: 'snapshot', accountId: 'account'));
    expect(PushRegistration.pendingTap, 'snapshot');
    await push.dispose();
    await messaging.dispose();
    api.close();
    PushRegistration.clearPendingTap();
  });

  test('sign-out removes a registration that finishes after stop', () async {
    final messaging = CountingMessaging();
    final api = RegistrationApi();
    final push = PushRegistration(api: api, messaging: messaging,
        platform: 'android', guest: false);
    final starting = push.start();
    await api.started.future;
    await push.stop();
    api.finish.complete();
    expect(await starting, isFalse);
    expect(push.registeredToken, isNull);
    expect(api.removed, ['token-a']);
    expect(messaging.activeRefreshListeners, isEmpty);
    await push.dispose();
    await messaging.dispose();
    api.close();
  });
}
