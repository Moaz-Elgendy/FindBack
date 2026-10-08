import 'dart:io';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/services/account_coordinator.dart';
import 'package:findback/services/auth_service.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class DeletionApi extends ApiClient {
  DeletionApi({super.tokens});
  bool failDeletion = false;
  int deletions = 0;
  @override Future<void> deleteAccount() async {
    if (failDeletion) throw ApiException('Deletion unavailable');
    deletions++;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });
  testWidgets('guest to A to B to logout to A preserves isolated caches and tokens', (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    final directory = (await tester.runAsync(() => Directory.systemTemp.createTemp('findback-scopes')))!;
    final auth = AuthService();
    Future<T> drive<T>(Future<T> future) async {
      var done = false;
      future.then<void>((_) => done = true, onError: (Object _, StackTrace __) { done = true; });
      for (var i = 0; i < 200 && !done; i++) {
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump();
      }
      expect(done, isTrue, reason: 'Platform and SQLite callbacks complete');
      return future;
    }
    final stores = <String?, TokenStore>{};
    final creating = AccountCoordinator.create(auth: auth,
      factory: (scope, guest, tokens) async {
        stores[scope] = tokens;
        final db = await LocalDb.openAt('${directory.path}/${scope ?? 'legacy'}.db');
        final api = DeletionApi(tokens: tokens);
        return AppServices(db: db, api: api, guest: guest,
          items: GuestLibrary(db, api).items,
          capture: CaptureService(ingest: (_, __, ___) async => throw UnimplementedError(),
            queue: (url, preview, hint) => db.queueSave(url: url), isOnline: () async => false),
          share: ShareIntentService(),
          sync: SyncService(pending: db.pendingQueue, send: (_) async => throw UnimplementedError(),
            apply: db.applyMapped, markFailed: db.markQueueFailed,
            changes: () => const Stream.empty(), isOnline: () async => false));
      });
    AccountCoordinator? created;
    creating.then((value) => created = value);
    for (var i = 0; i < 100 && created == null; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(created, isNotNull);
    final coordinator = created!;
    await drive(coordinator.services.db.queueSave(url: 'https://example.test/guest'));
    Future<void> change(String? id) async {
      auth.session.value = id == null ? null : AuthSession(id: id, email: '$id@example.test',
        accessToken: 'token-$id', refreshToken: 'refresh-$id', expiresAt: DateTime.now().add(const Duration(hours: 1)));
      var done = false;
      coordinator.settled.then((_) => done = true);
      for (var i = 0; i < 100 && !done; i++) {
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump();
      }
      expect(done, isTrue); expect(coordinator.error, isNull);
    }
    await change('a');
    expect(await drive(coordinator.services.db.pendingCount()), 1);
    expect(await stores['account-a']!.read(), 'token-a');
    await change('b');
    expect(await drive(coordinator.services.db.pendingCount()), 0);
    await expectLater(stores['account-a']!.read(), throwsA(isA<ApiException>()));
    await drive(coordinator.services.db.queueSave(url: 'https://example.test/b'));
    await change(null);
    expect(await drive(coordinator.services.db.pendingCount()), 0);
    await change('a');
    expect(await drive(coordinator.services.db.pendingCount()), 1);
    expect((await drive(coordinator.services.db.pendingQueue())).single.url, 'https://example.test/guest');
    final deletedApi = coordinator.services.api as DeletionApi;
    deletedApi.failDeletion = true;
    await expectLater(drive(coordinator.deleteAccount()), throwsA(isA<ApiException>()));
    expect(auth.currentSession!.id, 'a');
    expect(await drive(coordinator.services.db.pendingCount()), 1);
    deletedApi.failDeletion = false;
    await drive(coordinator.deleteAccount());
    expect(auth.currentSession, isNull);
    expect(deletedApi.deletions, 1);
    final erased = await drive(LocalDb.openAt('${directory.path}/account-a.db'));
    expect(await drive(erased.pendingCount()), 0);
    expect(await drive(erased.recentLocalItems()), isEmpty);
    await drive(erased.close());
    final preserved = await drive(LocalDb.openAt('${directory.path}/account-b.db'));
    expect(await drive(preserved.pendingCount()), 1);
    await drive(preserved.close());
    await tester.pumpWidget(const SizedBox());
    final closing = coordinator.close();
    var closed = false;
    closing.then((_) => closed = true);
    for (var i = 0; i < 100 && !closed; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(closed, isTrue);
    await tester.runAsync(() => directory.delete(recursive: true));
  });
}
