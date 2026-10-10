import 'dart:io';
import 'dart:async';
import 'package:findback/app.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/models/item.dart';
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

class RedeemingApi extends ApiClient {
  RedeemingApi(this.saved, {super.tokens, this.gate});
  final List<String> saved;
  final Completer<void>? gate;
  @override
  Future<ItemDetail> redeemShare(String token) async {
    saved.add(token);
    if (saved.length == 1 && gate != null) await gate!.future;
    return ItemDetail.fromJson({
      'id': 'recipient-copy', 'url': 'https://example.test/shared',
      'title': 'Shared snapshot title', 'summary': 'An independent copy',
      'instant_brief': 'An independent copy', 'shared_by': 'Alex',
      'brief_source': 'shared_snapshot', 'status': 'ready', 'key_points': [],
    });
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  for (final switchAccount in [false, true]) {
  testWidgets('pending token survives restart and sign-in; account switch $switchAccount', (tester) async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    FlutterSecureStorage.setMockInitialValues({});
    final token = List.filled(43, 'a').join();
    var nativeLink = 'https://findback.duckdns.org/s/$token';
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (call) async {
        if (call.method == 'getInitialAuthLink' && nativeLink.isNotEmpty) {
          final value = nativeLink;
          nativeLink = '';
          return value;
        }
        return null;
      });
    final directory = (await tester.runAsync(() => Directory.systemTemp.createTemp('findback-share')))!;
    Future<T> drive<T>(Future<T> future) async {
      var done = false;
      future.then<void>((_) => done = true, onError: (Object _, StackTrace __) { done = true; });
      for (var i = 0; i < 200 && !done; i++) {
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump(const Duration(milliseconds: 20));
      }
      expect(done, isTrue, reason: 'Platform and SQLite callbacks complete');
      return future;
    }
    final auth = AuthService();
    final saved = <String>[];
    final gate = switchAccount ? Completer<void>() : null;
    final accounts = (await tester.runAsync(() => AccountCoordinator.create(auth: auth,
      factory: (scope, guest, tokens) async {
        final db = await LocalDb.openAt('${directory.path}/${scope ?? 'legacy'}.db');
        final api = RedeemingApi(saved, tokens: tokens, gate: gate);
        return AppServices(db: db, api: api, guest: guest,
          items: GuestLibrary(db, api).items,
          capture: CaptureService(ingest: (_, __, ___) async => throw UnimplementedError(),
            queue: (url, preview, hint) => db.queueSave(url: url), isOnline: () async => false),
          share: ShareIntentService(),
          sync: SyncService(pending: db.pendingQueue, send: (_) async => throw UnimplementedError(),
            apply: db.applyMapped, markFailed: db.markQueueFailed,
            changes: () => const Stream.empty(), isOnline: () async => false));
      })))!;
    await tester.pumpWidget(FindBackApp(services: accounts.services, accounts: accounts));
    await drive(accounts.start());
    for (var i = 0; i < 30; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump(const Duration(milliseconds: 20));
    }
    expect(find.text('Sign in'), findsWidgets);
    const storage = FlutterSecureStorage();
    expect(await tester.runAsync(() => storage.read(key: 'findback.pendingShareToken.https://findback.duckdns.org')), token);
    expect(saved, isEmpty);
    await tester.pumpWidget(const SizedBox());
    auth.session.value = AuthSession(id: 'recipient', email: 'recipient@example.test',
      accessToken: 'token', refreshToken: 'refresh', expiresAt: DateTime.now().add(const Duration(hours: 1)));
    var switched = false;
    accounts.settled.then((_) => switched = true);
    for (var i = 0; i < 200 && !switched; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(switched, isTrue);
    await tester.pumpWidget(FindBackApp(services: accounts.services, accounts: accounts));
    if (switchAccount) {
      for (var i = 0; i < 100 && saved.isEmpty; i++) {
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump(const Duration(milliseconds: 20));
      }
      expect(saved, [token]);
      auth.session.value = AuthSession(id: 'recipient-two', email: 'two@example.test',
        accessToken: 'token-two', refreshToken: 'refresh-two', expiresAt: DateTime.now().add(const Duration(hours: 1)));
      await drive(accounts.settled);
      gate!.complete();
    }
    for (var i = 0; i < 100; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump(const Duration(milliseconds: 20));
      if (find.text('Shared by Alex').evaluate().isNotEmpty) break;
    }
    expect(find.text('Shared by Alex'), findsOneWidget);
    expect(saved, switchAccount ? [token, token] : [token]);
    expect(await tester.runAsync(() => storage.read(key: 'findback.pendingShareToken.https://findback.duckdns.org')), isNull);
    expect((await tester.runAsync(() => accounts.services.db.localItem('recipient-copy')))!.sharedBy, 'Alex');
    await tester.pumpWidget(const SizedBox());
    await drive(accounts.close());
    await tester.runAsync(() => directory.delete(recursive: true));
  });
  }
}
