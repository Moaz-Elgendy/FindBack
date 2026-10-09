/// Device registration: when the backend is told about this phone, and when it
/// is told to forget it.
///
/// A fake [MessagingService] stands in for Firebase throughout, so these tests
/// never touch the plugin, never need `google-services.json`, and never reach
/// a platform channel. That is the point of the interface in
/// `messaging_service.dart`.
///
/// The cases that matter are the ones where doing nothing is the correct
/// answer: notifications denied, no token, an unsupported platform, a guest
/// account. In all of them nothing is registered and nothing is thrown -- a
/// notification refusal must never surface as an error to the user.
library;

import 'dart:async';

import 'package:findback/data/api_client.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:flutter_test/flutter_test.dart';

/// Records every call the registration logic makes, and lets a test decide what
/// each one returns or throws.
class FakeMessaging implements MessagingService {
  FakeMessaging({this.currentToken = 'token-a', this.permission = true,
    this.available = true, this.tokenError});

  /// The token the fake platform will hand out; renamed from `token` so it
  /// does not collide with the `token()` method it overrides.
  String? currentToken;
  bool permission;
  bool available;
  Object? tokenError;
  final List<String> calls = [];
  final StreamController<String> refreshes = StreamController<String>.broadcast();
  final StreamController<PushPayload> foreground = StreamController<PushPayload>.broadcast();
  final StreamController<PushPayload> opened = StreamController<PushPayload>.broadcast();

  @override
  bool get isAvailable => available;

  @override
  Future<bool> requestPermission() async {
    calls.add('requestPermission');
    return permission;
  }

  @override
  Future<String?> token() async {
    calls.add('token');
    final failure = tokenError;
    if (failure != null) throw failure;
    return currentToken;
  }

  @override
  Stream<String> get onTokenRefresh => refreshes.stream;

  @override
  Stream<PushPayload> get onForegroundMessage => foreground.stream;

  @override
  Stream<PushPayload> get onNotificationOpened => opened.stream;

  PushPayload? initial;

  @override
  Future<PushPayload?> initialNotification() async {
    calls.add('initialNotification');
    return initial;
  }

  /// Deliver a foreground message (the app is visible).
  Future<void> deliverForeground(PushPayload payload) async {
    foreground.add(payload);
    await pumpEventQueue();
  }

  /// The user tapped the shade while the app was in the background.
  Future<void> openFromShade(PushPayload payload) async {
    opened.add(payload);
    await pumpEventQueue();
  }

  @override
  Future<void> dispose() async {
    await refreshes.close();
    await foreground.close();
    await opened.close();
  }

  /// Simulate the platform rotating the token, and wait for the listener.
  Future<void> rotate(String to) async {
    currentToken = to;
    refreshes.add(to);
    await pumpEventQueue();
  }
}

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

/// Records what the app asked the backend to do, without a server.
class _RecordingApi extends ApiClient {
  _RecordingApi() : super(tokens: _Tokens());

  final List<String> registered = [];
  final List<String> removed = [];
  Object? registerError;
  Object? removeError;
  int status = 201;

  @override
  Future<void> registerDevice(String token, String platform) async {
    if (registerError != null) throw registerError!;
    registered.add('$token:$platform');
  }

  @override
  Future<void> removeDevice(String token) async {
    if (removeError != null) throw removeError!;
    removed.add(token);
  }
}

void main() {
  late _RecordingApi api;
  late FakeMessaging messaging;
  late PushRegistration push;

  PushRegistration build({String platform = 'android', bool guest = false}) =>
      PushRegistration(api: api, messaging: messaging, platform: platform,
          guest: guest);

  setUp(() {
    api = _RecordingApi();
    messaging = FakeMessaging();
    push = build();
  });

  tearDown(() async => await messaging.dispose());

  group('signing in', () {
    test('registers the token with the platform for this device', () async {
      expect(await push.start(), isTrue);
      expect(api.registered, ['token-a:android']);
      expect(push.registeredToken, 'token-a');
    });

    test('reports the ios platform, not android', () async {
      final ios = build(platform: 'ios');
      expect(await ios.start(), isTrue);
      expect(api.registered, ['token-a:ios']);
    });

    test('asks for permission before it asks for a token', () async {
      await push.start();
      // The launch notification is read first, because a terminated-start tap
      // is a navigation and must not wait on a permission round trip.
      expect(messaging.calls,
          ['initialNotification', 'requestPermission', 'token']);
    });

    test('re-registering on every launch does not duplicate anything',
        () async {
      // The backend upserts on the token, so a repeated call is safe and the
      // app is expected to make one on each start.
      await push.start();
      await push.stop();
      await push.start();
      expect(api.registered, ['token-a:android', 'token-a:android']);
      expect(api.removed, ['token-a']);
    });
  });

  group('permission denied', () {
    test('skips registration without throwing', () async {
      messaging.permission = false;
      expect(await push.start(), isFalse);
      expect(api.registered, isEmpty);
      expect(push.registeredToken, isNull);
    });

    test('never even asks for the token once permission is refused', () async {
      messaging.permission = false;
      await push.start();
      expect(messaging.calls, isNot(contains('token')));
    });

    test('a later start registers once permission is granted', () async {
      messaging.permission = false;
      await push.start();
      messaging.permission = true;
      expect(await push.start(), isTrue);
      expect(api.registered, ['token-a:android']);
    });
  });

  group('nothing to register', () {
    test('a null token registers nothing', () async {
      messaging.currentToken = null;
      expect(await push.start(), isFalse);
      expect(api.registered, isEmpty);
    });

    test('an empty token registers nothing', () async {
      messaging.currentToken = '';
      expect(await push.start(), isFalse);
      expect(api.registered, isEmpty);
    });

    test('an unsupported platform registers nothing', () async {
      // The backend's platform vocabulary is closed; sending anything else
      // would be rejected, so there is no reason to send it.
      final desktop = build(platform: '');
      expect(await desktop.start(), isFalse);
      expect(api.registered, isEmpty);
    });

    test('a guest account is never registered', () async {
      final guestPush = build(guest: true);
      expect(await guestPush.start(), isFalse);
      expect(api.registered, isEmpty);
      // A guest still listens for taps -- that is how a signed-out tap gets
      // held for later -- but never asks for a token or registers anything.
      expect(messaging.calls, ['initialNotification']);
      expect(messaging.calls, isNot(contains('token')));
    });

    test('a messaging service that throws does not break the sign-in',
        () async {
      messaging.tokenError = Exception('plugin exploded');
      expect(await push.start(), isFalse);
      expect(api.registered, isEmpty);
    });
  });

  group('token refresh', () {
    test('re-registers the rotated token', () async {
      await push.start();
      await messaging.rotate('token-b');
      expect(api.registered, ['token-a:android', 'token-b:android']);
      expect(push.registeredToken, 'token-b');
    });

    test('the rotated token is the one later removed on sign-out', () async {
      await push.start();
      await messaging.rotate('token-b');
      await push.stop();
      expect(api.removed, ['token-b'], reason:
          'the old token is dead; deleting it would ask the backend to remove '
          'a registration that has already moved on');
    });

    test('a failed refresh leaves the previous registration alone', () async {
      await push.start();
      api.registerError = ApiException('server said no', kind: ApiFailureKind.server);
      await messaging.rotate('token-b');
      expect(api.registered, ['token-a:android'], reason:
          'a rejected refresh must not record the new token as registered');
      api.registerError = null;
      await push.stop();
      expect(api.removed, isEmpty, reason:
          'token-b never registered, so there is nothing of ours to delete');
    });
  });

  group('signing out', () {
    test('removes the token from the backend', () async {
      await push.start();
      await push.stop();
      expect(api.removed, ['token-a']);
      expect(push.registeredToken, isNull);
    });

    test('removes only the token this device registered', () async {
      messaging.currentToken = 'token-a';
      await push.start();
      await push.stop();
      expect(api.removed, ['token-a']);
    });

    test('a stop with nothing registered sends nothing', () async {
      await push.stop();
      expect(api.removed, isEmpty);
    });

    test('stopping twice does not delete twice', () async {
      await push.start();
      await push.stop();
      await push.stop();
      expect(api.removed, ['token-a']);
    });

    test('a failed removal does not block the sign-out', () async {
      await push.start();
      api.removeError = ApiException('offline', kind: ApiFailureKind.connectivity);
      await push.stop(); // must not throw
      expect(push.registeredToken, isNull);
    });

    test('a refresh after sign-out does not re-register', () async {
      await push.start();
      await push.stop();
      await messaging.rotate('token-b');
      expect(api.registered, ['token-a:android'], reason:
          'a signed-out device must not put itself back on the list');
    });
  });
}