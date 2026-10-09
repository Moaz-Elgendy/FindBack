/// `start()` is idempotent, because it runs on every app resume.
///
/// The bug these lock down: `start()` assigned `_refresh` without cancelling a
/// previous subscription, and `_resumePush` in `app.dart` calls `start()` on
/// every `AppLifecycleState.resumed`. Each resume abandoned a live subscription
/// and started another, so after five resumes a single token rotation fired
/// five `POST /api/v1/devices` calls.
///
/// The counting is on ACTIVE subscriptions, not on how many times `listen()`
/// was called, because that is the number that decides how many POSTs a
/// rotation produces. `Stream.multi` invokes its factory once per subscriber,
/// which is what makes an exact count possible.
library;

import 'dart:async';

import 'package:findback/data/api_client.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:flutter_test/flutter_test.dart';

/// A messaging service that can say exactly how many subscriptions are live.
class CountingMessaging implements MessagingService {
  CountingMessaging({this.currentToken = 'token-a', this.permission = true});

  String? currentToken;
  bool permission;
  final List<String> calls = [];

  final StreamController<String> _rotations = StreamController<String>.broadcast();

  /// Ids of every subscription that is currently listening.
  final Set<int> activeRefreshListeners = {};
  int _nextId = 0;

  @override
  bool get isAvailable => true;

  @override
  Future<bool> requestPermission() async {
    calls.add('requestPermission');
    return permission;
  }

  @override
  Future<String?> token() async {
    calls.add('token');
    return currentToken;
  }

  /// Non-broadcast `Stream.multi`, so the factory runs once PER SUBSCRIBER
  /// and the count below is the number of live listeners rather than a 0/1
  /// flag. A broadcast stream could only report "someone is listening", which
  /// is exactly the distinction this test needs to make.
  @override
  Stream<String> get onTokenRefresh => Stream<String>.multi((multi) {
        final id = _nextId++;
        activeRefreshListeners.add(id);
        final upstream = _rotations.stream.listen(multi.add, onDone: multi.close);
        multi.onCancel = () {
          activeRefreshListeners.remove(id);
          upstream.cancel();
        };
      });

  @override
  Stream<PushPayload> get onForegroundMessage => const Stream.empty();

  @override
  Stream<PushPayload> get onNotificationOpened => const Stream.empty();

  @override
  Future<PushPayload?> initialNotification() async => null;

  @override
  Future<void> dispose() async => _rotations.close();

  /// Rotate the token and let every live listener react.
  Future<void> rotate(String to) async {
    currentToken = to;
    _rotations.add(to);
    await pumpEventQueue();
  }
}

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

class _RecordingApi extends ApiClient {
  _RecordingApi() : super(tokens: _Tokens());
  final List<String> registered = [];
  @override
  Future<void> registerDevice(String token, String platform) async =>
      registered.add('$token:$platform');
  @override
  Future<void> removeDevice(String token) async {}
}

void main() {
  late _RecordingApi api;
  late CountingMessaging messaging;
  late PushRegistration push;

  setUp(() {
    api = _RecordingApi();
    messaging = CountingMessaging();
    push = PushRegistration(
        api: api, messaging: messaging, platform: 'android', guest: false);
  });

  tearDown(() async => await messaging.dispose());

  test('five starts leave exactly one active listener', () async {
    for (var i = 0; i < 5; i++) {
      expect(await push.start(), isTrue);
    }
    expect(messaging.activeRefreshListeners, hasLength(1),
        reason: 'each resume must not start another subscription');
  });

  test('a rotation after five starts registers exactly once', () async {
    for (var i = 0; i < 5; i++) {
      await push.start();
    }
    final before = api.registered.length;

    await messaging.rotate('token-b');

    expect(api.registered.length - before, 1,
        reason: 'one rotation must produce one POST, not one per start');
    expect(api.registered.last, 'token-b:android');
    expect(push.registeredToken, 'token-b');
  });

  test('stop leaves no listener behind', () async {
    for (var i = 0; i < 5; i++) {
      await push.start();
    }
    expect(messaging.activeRefreshListeners, hasLength(1));

    await push.stop();

    expect(messaging.activeRefreshListeners, isEmpty);
  });

  test('a rotation after stop does nothing', () async {
    await push.start();
    await push.stop();
    final before = api.registered.length;

    await messaging.rotate('token-b');

    expect(api.registered.length, before,
        reason: 'a signed-out device must not put itself back on the list');
  });

  test('starting again after stop subscribes once more, not twice', () async {
    for (var i = 0; i < 3; i++) {
      await push.start();
      await push.stop();
    }
    await push.start();

    expect(messaging.activeRefreshListeners, hasLength(1));
    await messaging.rotate('token-b');
    expect(api.registered.last, 'token-b:android');
  });

  test('a resume still registers the current token', () async {
    await push.start();
    expect(api.registered, ['token-a:android']);

    // The idempotency guard is on the LISTENER only. Registration must still
    // run every time, because permission may have been granted in system
    // settings and the backend may have lost the row.
    await push.start();
    expect(api.registered, ['token-a:android', 'token-a:android']);
    expect(messaging.activeRefreshListeners, hasLength(1));
  });

  test('a rotation is registered even after several resumes', () async {
    for (var i = 0; i < 4; i++) {
      await push.start();
    }
    // Four resumes, each an idempotent upsert of the same token.
    expect(api.registered, hasLength(4));
    expect(api.registered.toSet(), {'token-a:android'});

    await messaging.rotate('token-b');
    await messaging.rotate('token-c');

    // The property that matters: one POST per rotation, whatever the resume
    // count. Before the fix this would be four each.
    expect(api.registered.where((r) => r == 'token-b:android'), hasLength(1));
    expect(api.registered.where((r) => r == 'token-c:android'), hasLength(1));
    expect(push.registeredToken, 'token-c');
  });

  test('a denied permission leaves no listener', () async {
    messaging.permission = false;
    expect(await push.start(), isFalse);
    expect(messaging.activeRefreshListeners, isEmpty,
        reason: 'a user who refused must not be registered by a later rotation');

    // Granting it later and starting again subscribes exactly once.
    messaging.permission = true;
    for (var i = 0; i < 3; i++) {
      await push.start();
    }
    expect(messaging.activeRefreshListeners, hasLength(1));
    expect(api.registered, ['token-a:android', 'token-a:android', 'token-a:android']);
  });

  test('a guest never subscribes', () async {
    final guest = PushRegistration(
        api: api, messaging: messaging, platform: 'android', guest: true);
    expect(await guest.start(), isFalse);
    expect(messaging.activeRefreshListeners, isEmpty);
    await messaging.rotate('token-b');
    expect(api.registered, isEmpty);
  });
}