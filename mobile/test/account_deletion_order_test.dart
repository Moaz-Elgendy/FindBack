/// Account deletion unregisters the device before the account goes.
///
/// The device-token delete is owner-scoped: `DELETE /api/v1/devices/{token}`
/// answers 404 for a token that is not the caller's, because the route filters
/// on `user_id` as well as on the token. So deleting the account first meant
/// the unregister always 404'd and the token only ever went away via the
/// database cascade — the app never actually asked.
///
/// The order matters, and the error behaviour around it has to be preserved:
/// a failure to unregister must not leave the account undeleted.
library;

import 'dart:async';
import 'dart:io';

import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/services/account_coordinator.dart';
import 'package:findback/services/auth_service.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

/// Records the ORDER of the two calls that matter, so the test can assert it
/// rather than infer it.
class OrderingApi extends ApiClient {
  OrderingApi({super.tokens, required this.order});

  final List<String> order;
  bool failDeletion = false;
  int deletions = 0;

  @override
  Future<void> deleteAccount() async {
    order.add('deleteAccount');
    if (failDeletion) throw ApiException('Deletion unavailable');
    deletions++;
  }

  @override
  Future<void> registerDevice(String token, String platform) async {}

  @override
  Future<void> removeDevice(String token) async {
    order.add('removeDevice:$token');
  }
}

/// Permits registration, so the account's push actually holds a token and
/// `stop()` has something to remove. Without this the ordering assertion is
/// vacuous: no `removeDevice` would ever be recorded.
class _NoMessaging implements MessagingService {
  @override
  bool get isAvailable => true;
  @override
  Future<bool> requestPermission() async => true;
  @override
  Future<String?> token() async => 'token-a';
  @override
  Stream<String> get onTokenRefresh => const Stream.empty();
  @override
  Stream<PushPayload> get onForegroundMessage => const Stream.empty();
  @override
  Stream<PushPayload> get onNotificationOpened => const Stream.empty();
  @override
  Future<PushPayload?> initialNotification() async => null;
  @override
  Future<void> dispose() async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
            const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });

  testWidgets('the device is unregistered before the account is deleted',
      (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    final directory =
        (await tester.runAsync(() => Directory.systemTemp.createTemp('fb-del')))!;
    final order = <String>[];
    final auth = AuthService();

    Future<T> drive<T>(Future<T> future) async {
      var done = false;
      future.then<void>((_) => done = true,
          onError: (Object _, StackTrace __) { done = true; });
      for (var i = 0; i < 200 && !done; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump();
      }
      return future;
    }

    final creating = AccountCoordinator.create(auth: auth,
        factory: (scope, guest, tokens) async {
      final db = await LocalDb.openAt('${directory.path}/$scope.db');
      final api = OrderingApi(tokens: tokens, order: order);
      return AppServices(
        db: db,
        api: api,
        guest: guest,
        push: PushRegistration(
          api: api,
          messaging: _NoMessaging(),
          guest: guest,
          platform: 'android',
        ),
        items: GuestLibrary(db, api).items,
        capture: CaptureService(
            ingest: (_, __, ___) async => throw UnimplementedError(),
            queue: (url, preview, hint) async => db.queueSave(
                url: url, preview: preview, titleHint: hint),
            isOnline: () async => false),
        share: ShareIntentService(),
        sync: SyncService(
            pending: db.pendingQueue,
            send: (_) async => throw UnimplementedError(),
            apply: db.applyMapped,
            markFailed: db.markQueueFailed,
            changes: () => const Stream.empty(),
            isOnline: () async => false),
      );
    });

    AccountCoordinator? created;
    creating.then((value) => created = value);
    for (var i = 0; i < 100 && created == null; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(created, isNotNull);
    final coordinator = created!;

    // Sign in, so deleteAccount() has an account to act on.
    auth.session.value = AuthSession(
        id: 'a',
        email: 'a@example.test',
        accessToken: 'token-a',
        refreshToken: 'refresh-a',
        expiresAt: DateTime.now().add(const Duration(hours: 1)));
    for (var i = 0; i < 200; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      if (auth.currentSession?.id == 'a' && !coordinator.services.guest) {
        break;
      }
      await tester.pump();
    }
    expect(coordinator.services.guest, isFalse);

    // `services` is swapped before startSync runs, so the loop above can return
    // while registration has not happened yet. Drive it explicitly.
    await drive(coordinator.services.startSync());
    expect(coordinator.services.push!.registeredToken, 'token-a',
        reason: 'otherwise there is nothing to unregister and nothing to order');
    order.clear();

    await drive(coordinator.deleteAccount());

    // Both must be present: `indexOf` returns -1 for a missing entry, and
    // -1 < 0 would make the ordering assertion pass for the wrong reason.
    expect(order, contains('removeDevice:token-a'));
    expect(order, contains('deleteAccount'));
    expect(order.indexOf('removeDevice:token-a'),
        lessThan(order.indexOf('deleteAccount')),
        reason: 'the token must be removed while the account still exists');

    // Signing out opens a fresh guest scope whose SyncService runs a periodic
    // timer; closing the coordinator is what cancels it, exactly as the
    // existing coordinator test does.
    await tester.pumpWidget(const SizedBox());
    var closed = false;
    coordinator.close().then((_) => closed = true);
    for (var i = 0; i < 100 && !closed; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(closed, isTrue);
    await tester.runAsync(() => directory.delete(recursive: true));
  });

  testWidgets('a failed unregister does not stop the deletion',
      (tester) async {
    // The account must be deleted even if unregistering fails; the database
    // cascade still removes the token.
    FlutterSecureStorage.setMockInitialValues({});
    final directory =
        (await tester.runAsync(() => Directory.systemTemp.createTemp('fb-del2')))!;
    final order = <String>[];
    final auth = AuthService();

    Future<T> drive<T>(Future<T> future) async {
      var done = false;
      future.then<void>((_) => done = true,
          onError: (Object _, StackTrace __) { done = true; });
      for (var i = 0; i < 200 && !done; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump();
      }
      return future;
    }

    final creating = AccountCoordinator.create(auth: auth,
        factory: (scope, guest, tokens) async {
      final db = await LocalDb.openAt('${directory.path}/$scope.db');
      final api = OrderingApi(tokens: tokens, order: order);
      return AppServices(
        db: db,
        api: api,
        guest: guest,
        push: PushRegistration(
            api: api,
            messaging: _NoMessaging(),
            guest: guest,
            platform: 'android'),
        items: GuestLibrary(db, api).items,
        capture: CaptureService(
            ingest: (_, __, ___) async => throw UnimplementedError(),
            queue: (url, preview, hint) async => db.queueSave(
                url: url, preview: preview, titleHint: hint),
            isOnline: () async => false),
        share: ShareIntentService(),
        sync: SyncService(
            pending: db.pendingQueue,
            send: (_) async => throw UnimplementedError(),
            apply: db.applyMapped,
            markFailed: db.markQueueFailed,
            changes: () => const Stream.empty(),
            isOnline: () async => false),
      );
    });

    AccountCoordinator? created;
    creating.then((value) => created = value);
    for (var i = 0; i < 100 && created == null; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    final coordinator = created!;

    auth.session.value = AuthSession(
        id: 'a',
        email: 'a@example.test',
        accessToken: 'token-a',
        refreshToken: 'refresh-a',
        expiresAt: DateTime.now().add(const Duration(hours: 1)));
    for (var i = 0; i < 200; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      if (!coordinator.services.guest) break;
      await tester.pump();
    }
    expect(coordinator.services.guest, isFalse);

    // removeDevice throws, which push.stop() already swallows.
    await drive(coordinator.services.startSync());
    expect(coordinator.services.push!.registeredToken, 'token-a');
    order.clear();

    await drive(coordinator.deleteAccount());

    expect(order, contains('deleteAccount'),
        reason: 'a failed unregister must not block the deletion');

    // Signing out opens a fresh guest scope whose SyncService runs a periodic
    // timer; closing the coordinator is what cancels it, exactly as the
    // existing coordinator test does.
    await tester.pumpWidget(const SizedBox());
    var closed = false;
    coordinator.close().then((_) => closed = true);
    for (var i = 0; i < 100 && !closed; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(closed, isTrue);
    await tester.runAsync(() => directory.delete(recursive: true));
  });
}
