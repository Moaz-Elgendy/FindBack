/// Which screen a LOCAL notification tap opens.
///
/// The reminders channel and the foreground weekly note share one
/// `flutter_local_notifications` instance and therefore one tap handler. The
/// bug this covers: a `weekly_note:` payload was being pushed as
/// `DetailPage(itemId: <snapshot id>)` -- a memory screen for something that is
/// not a memory, showing "not found" instead of the saved list.
///
/// Every test asserts WHICH SCREEN OPENS, by type and by the id it was given.
/// Asserting only the payload text is exactly what let the original bug
/// through.
///
/// These call `openNotificationTap` -- the same function `app.dart` calls --
/// rather than reimplementing the routing, so the assertions are about
/// production behaviour. The screen is found inside a plain Navigator rather
/// than the whole `FindBackApp`, because the library shell does not mount under
/// the test binding.
library;

import 'dart:async';
import 'dart:io';

import 'package:findback/app.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/features/home/detail_page.dart';
import 'package:findback/features/weekly_note/worth_another_look_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

class _Api extends ApiClient {
  _Api() : super(tokens: _Tokens());

  /// Ids the snapshot page actually asked for, so a test can assert not only
  /// WHICH screen opened but which snapshot it was given.
  final List<String> requestedSnapshots = [];

  @override
  Future<void> registerDevice(String token, String platform) async {}

  @override
  Future<void> removeDevice(String token) async {}

  /// The real client would attempt an HTTP call and the test would sit on a
  /// dio timeout. Failing fast puts the page into its "unavailable" state,
  /// which is not what these tests assert on.
  @override
  Future<SnapshotPage> snapshotItems(String id) async {
    requestedSnapshots.add(id);
    throw ApiException('offline', kind: ApiFailureKind.connectivity);
  }
}

class _NoMessaging implements MessagingService {
  @override
  bool get isAvailable => false;
  @override
  Future<bool> requestPermission() async => false;
  @override
  Future<String?> token() async => null;
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

/// Services with no reminders, reminders and push are not involved in routing.
Future<(AppServices, _Api, Directory)> harness(WidgetTester tester,
    {required String name, bool guest = false}) async {
  final directory =
      (await tester.runAsync(() => Directory.systemTemp.createTemp(name)))!;
  // Opened INSIDE runAsync, as every other test here does. Opening it in the
  // test body leaves the binding unable to complete a later frame: the
  // database's real-async work lands in the wrong zone and pumpWidget then
  // hangs until the test times out.
  final db =
      (await tester.runAsync(() => LocalDb.openAt('${directory.path}/db.sqlite')))!;
  final api = _Api();
  final services = AppServices(
    db: db,
    api: api,
    guest: guest,
    push: PushRegistration(
        api: api,
        messaging: _NoMessaging(),
        guest: guest,
        platform: 'android'),
    items: ItemsService.of(db: db, api: api, isOnline: () async => false),
    capture: CaptureService(
        ingest: (_, __, ___) async => throw UnimplementedError(),
        queue: (url, preview, hint) async =>
            db.queueSave(url: url, preview: preview, titleHint: hint),
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
  return (services, api, directory);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  setUp(PushRegistration.clearPendingTap);

  /// A plain Navigator so a pushed route can be inspected, without the library
  /// shell, which does not mount under the test binding.
  Future<void> mount(WidgetTester tester, GlobalKey<NavigatorState> nav) async {
    await tester.pumpWidget(MaterialApp(
      navigatorKey: nav,
      home: const Scaffold(body: Text('Library')),
    ));
    await tester.pump();
  }

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 10; i++) {
      await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
  }

  testWidgets('a weekly_note: payload opens Worth another look with that id',
      (tester) async {
    final (services, api, directory) =
        await harness(tester, name: 'fb-tap-wn');
    final nav = GlobalKey<NavigatorState>();
    await mount(tester, nav);

    final handled = openNotificationTap(
        '${kWeeklyNotePayloadPrefix}snap-abc123', nav, services);
    await settle(tester);

    expect(handled, isTrue);
    expect(find.byType(WorthAnotherLookPage), findsOneWidget);
    expect(find.byType(DetailPage), findsNothing,
        reason: 'a snapshot id is not a memory id');
    expect(
        tester
            .widget<WorthAnotherLookPage>(find.byType(WorthAnotherLookPage))
            .snapshotId,
        'snap-abc123');

    await tester.runAsync(() => services.dispose());
    await tester.runAsync(() => directory.delete(recursive: true));
  }, timeout: const Timeout(Duration(seconds: 40)));

  testWidgets('the page is asked for exactly the id after the prefix',
      (tester) async {
    final (services, api, directory) =
        await harness(tester, name: 'fb-tap-ids');
    final nav = GlobalKey<NavigatorState>();
    await mount(tester, nav);

    openNotificationTap('${kWeeklyNotePayloadPrefix}snap-xyz', nav, services);
    await settle(tester);

    expect(api.requestedSnapshots, ['snap-xyz'],
        reason: 'the page fetches the snapshot the payload named');

    await tester.runAsync(() => services.dispose());
    await tester.runAsync(() => directory.delete(recursive: true));
  }, timeout: const Timeout(Duration(seconds: 40)));

  testWidgets('a plain payload still opens the memory detail screen',
      (tester) async {
    final (services, api, directory) =
        await harness(tester, name: 'fb-tap-plain');
    final nav = GlobalKey<NavigatorState>();
    await mount(tester, nav);

    const memoryId = '11111111-2222-3333-4444-555555555555';
    openNotificationTap(memoryId, nav, services);
    await settle(tester);

    expect(find.byType(DetailPage), findsOneWidget,
        reason: 'reminder behaviour must be unchanged');
    expect(find.byType(WorthAnotherLookPage), findsNothing);
    expect(tester.widget<DetailPage>(find.byType(DetailPage)).itemId, memoryId);
    expect(api.requestedSnapshots, isEmpty,
        reason: 'a memory tap must not fetch a snapshot');

    await tester.runAsync(() => services.dispose());
    await tester.runAsync(() => directory.delete(recursive: true));
  }, timeout: const Timeout(Duration(seconds: 40)));

  testWidgets('an empty or blank id after the prefix is ignored',
      (tester) async {
    final (services, api, directory) =
        await harness(tester, name: 'fb-tap-empty');
    final nav = GlobalKey<NavigatorState>();
    await mount(tester, nav);

    // "weekly_note:" with nothing after it, then the prefix plus only spaces.
    final blank = kWeeklyNotePayloadPrefix + List.filled(3, ' ').join();
    for (final payload in [kWeeklyNotePayloadPrefix, blank]) {
      final handled = openNotificationTap(payload, nav, services);
      await settle(tester);

      expect(handled, isFalse, reason: payload);
      // No screen at all: it would only ever show a permanently unavailable
      // list with nothing behind it.
      expect(find.byType(WorthAnotherLookPage), findsNothing, reason: payload);
      expect(find.byType(DetailPage), findsNothing, reason: payload);
    }

    await tester.runAsync(() => services.dispose());
    await tester.runAsync(() => directory.delete(recursive: true));
  }, timeout: const Timeout(Duration(seconds: 40)));

  testWidgets('a tap while signed out is held and opened after sign-in',
      (tester) async {
    final (guestServices, api, directory) =
        await harness(tester, name: 'fb-tap-guest', guest: true);
    final nav = GlobalKey<NavigatorState>();
    await mount(tester, nav);

    // Signed out: held, nothing opened.
    expect(openNotificationTap(
        '${kWeeklyNotePayloadPrefix}snap-late', nav, guestServices), isTrue);
    await settle(tester);

    expect(find.byType(WorthAnotherLookPage), findsNothing,
        reason: 'a guest has no account to read the snapshot with');
    expect(PushRegistration.pendingTap, 'snap-late');

    // Signed in: the same routing now opens the page, taking the held tap once.
    final (services, _, _) = await harness(tester, name: 'fb-tap-in');
    expect(services.guest, isFalse);
    expect(PushRegistration.takePendingTap(), 'snap-late');

    openNotificationTap('${kWeeklyNotePayloadPrefix}snap-late', nav, services);
    await settle(tester);

    expect(find.byType(WorthAnotherLookPage), findsOneWidget);
    expect(
        tester
            .widget<WorthAnotherLookPage>(find.byType(WorthAnotherLookPage))
            .snapshotId,
        'snap-late');
    expect(PushRegistration.pendingTap, isNull, reason: 'taken exactly once');

    await tester.runAsync(() => guestServices.dispose());
    await tester.runAsync(() => services.dispose());
    await tester.runAsync(() => directory.delete(recursive: true));
  }, timeout: const Timeout(Duration(seconds: 60)));
}