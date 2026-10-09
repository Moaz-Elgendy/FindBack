/// Turning a tap on the weekly note into the "Worth another look" screen.
///
/// The three ways a notification can be tapped are genuinely different code
/// paths in Firebase -- a stream while backgrounded, a one-shot read when the
/// tap is what launched the app, and nothing at all in the foreground -- so
/// each is tested separately against a fake.
///
/// The security-relevant cases are here too. A tap carries no account id, so
/// the app must not infer ownership from the snapshot id: it asks the server,
/// and a snapshot belonging to somebody else answers 404. And a tap that
/// arrives while signed out has to survive, not be dropped.
library;

import 'dart:async';

import 'package:dio/dio.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/features/weekly_note/worth_another_look_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:findback/services/messaging_service.dart';
import 'package:findback/services/push_registration.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const String snapshotId = 'snap-7';
const String memoryTitle = 'Sourdough basics';

typedef _Responder = FutureOr<Response<dynamic>> Function(RequestOptions request);


class FakeMessaging implements MessagingService {
  FakeMessaging({this.currentToken = 'token-a', this.permission = true,
    this.initial});

  String? currentToken;
  bool permission;
  PushPayload? initial;
  final List<String> calls = [];
  final StreamController<String> refreshes = StreamController<String>.broadcast();
  final StreamController<PushPayload> foreground = StreamController<PushPayload>.broadcast();
  final StreamController<PushPayload> opened = StreamController<PushPayload>.broadcast();

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

  @override
  Stream<String> get onTokenRefresh => refreshes.stream;

  @override
  Stream<PushPayload> get onForegroundMessage => foreground.stream;

  @override
  Stream<PushPayload> get onNotificationOpened => opened.stream;

  @override
  Future<PushPayload?> initialNotification() async {
    calls.add('initialNotification');
    return initial;
  }

  @override
  Future<void> dispose() async {
    await refreshes.close();
    await foreground.close();
    await opened.close();
  }
}

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => 'access-token';
}

/// Stands in for the app's data layer, so a tap can be driven without a
/// database or a server.
class _StubApi extends ApiClient {
  _StubApi() : super(tokens: _Tokens());
  final List<String> registered = [];
  @override
  Future<void> registerDevice(String token, String platform) async =>
      registered.add('$token:$platform');
  @override
  Future<void> removeDevice(String token) async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  /// The widget tests below run real SQLite statements, which need the FFI
  /// factory; without this the database open fails and the page shows the wrong
  /// state for the wrong reason.
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  late _StubApi api;
  late FakeMessaging messaging;
  late List<String> opened;

  PushRegistration build({bool guest = false, String? accountId}) =>
      PushRegistration(api: api, messaging: messaging, platform: 'android',
          guest: guest, accountId: accountId);

  setUp(() {
    api = _StubApi();
    messaging = FakeMessaging();
    opened = [];
    PushRegistration.clearPendingTap();
  });

  tearDown(() async => await messaging.dispose());

  /// Start a service with a listener attached, which is what the UI does.
  Future<PushRegistration> started({bool guest = false, String? accountId}) async {
    final push = build(guest: guest, accountId: accountId);
    push.taps.listen(opened.add);
    await push.start();
    // The terminated-launch read is a future; let it land before asserting.
    await pumpEventQueue();
    return push;
  }

  const weekly = PushPayload(type: 'weekly_note', snapshotId: snapshotId);

  // --- 1. background: the user tapped the shade ---------------------------

  group('background tap', () {
    test('a tap from the shade opens the snapshot', () async {
      await started();
      messaging.opened.add(weekly);
      await pumpEventQueue();
      expect(opened, [snapshotId]);
    });

    test('two taps open two snapshots', () async {
      await started();
      messaging.opened.add(weekly);
      messaging.opened
          .add(const PushPayload(type: 'weekly_note', snapshotId: 'snap-8'));
      await pumpEventQueue();
      expect(opened, [snapshotId, 'snap-8']);
    });
  });

  // --- 2. terminated: the tap is what launched the app --------------------

  group('terminated launch', () {
    // The UI binds `taps` before calling start(), so the notification that
    // launched the app reaches the same handler as a background tap. One path,
    // not two, and therefore nothing to drain afterwards.
    test('the notification that launched the app opens the snapshot',
        () async {
      messaging.initial = weekly;
      await started();
      expect(opened, [snapshotId]);
    });

    test('no launch notification opens nothing', () async {
      await started();
      expect(opened, isEmpty);
    });

    test('the launch notification is read exactly once per start', () async {
      messaging.initial = weekly;
      await started();
      expect(messaging.calls.where((c) => c == 'initialNotification'), hasLength(1));
    });
  });

  // --- 3. foreground: the notification is left alone -----------------------

  group('foreground message', () {
    test('does not navigate', () async {
      await started();
      messaging.foreground.add(weekly);
      await pumpEventQueue();
      expect(opened, isEmpty, reason:
          'the user did not tap anything; moving them would be a surprise');
    });

    test('does not hold a tap for later either', () async {
      await started();
      messaging.foreground.add(weekly);
      await pumpEventQueue();
      expect(PushRegistration.pendingTap, isNull);
    });

    test('a foreground message does not shadow a later real tap', () async {
      await started();
      messaging.foreground.add(weekly);
      messaging.opened.add(weekly);
      await pumpEventQueue();
      expect(opened, [snapshotId]);
    });
  });

  // --- 4. only the weekly note is acted on --------------------------------

  group('message types', () {
    test('an unknown type is ignored', () async {
      await started();
      messaging.opened.add(
          const PushPayload(type: 'something_else', snapshotId: snapshotId));
      await pumpEventQueue();
      expect(opened, isEmpty);
    });

    test('an unknown type launched the app is ignored too', () async {
      messaging.initial =
          const PushPayload(type: 'account_alert', snapshotId: snapshotId);
      await started();
      expect(opened, isEmpty);
    });

    test('a weekly note without a snapshot id is ignored', () async {
      // A payload with no id cannot address anything; parsing rejects it before
      // it ever reaches here, so this proves the guard is not the only defence.
      await started();
      expect(PushPayload.fromData({'type': 'weekly_note'}), isNull);
      expect(PushPayload.fromData({'snapshot_id': snapshotId}), isNull);
      expect(PushPayload.fromData(null), isNull);
    });

    test('a well formed weekly note payload parses', () {
      expect(
          PushPayload.fromData({
            'type': 'weekly_note',
            'snapshot_id': snapshotId
          }),
          weekly);
    });
  });

  // --- 5. signed out: hold the tap, replay it after signing in ------------

  group('signed out', () {
    test('a tap while signed out is held rather than opened', () async {
      await started(guest: true);
      messaging.opened.add(weekly);
      await pumpEventQueue();
      expect(opened, isEmpty);
      expect(PushRegistration.pendingTap, snapshotId);
    });

    test('the held tap is taken exactly once after signing in', () async {
      await started(guest: true);
      messaging.opened.add(weekly);
      await pumpEventQueue();

      expect(PushRegistration.takePendingTap(), snapshotId);
      expect(PushRegistration.takePendingTap(), isNull,
          reason: 'replaying the same tap twice would open two screens');
    });

    test('a second tap replaces the held one', () async {
      await started(guest: true);
      messaging.opened.add(weekly);
      messaging.opened
          .add(const PushPayload(type: 'weekly_note', snapshotId: 'snap-9'));
      await pumpEventQueue();
      expect(PushRegistration.pendingTap, 'snap-9',
          reason: 'only the most recent intent is still current');
    });

    test('a terminated launch while signed out is held too', () async {
      messaging.initial = weekly;
      await started(guest: true);
      await pumpEventQueue();
      expect(PushRegistration.pendingTap, snapshotId);
    });

    test('nothing is held when there was no tap', () async {
      await started(guest: true);
      expect(PushRegistration.pendingTap, isNull);
    });

    test('the held tap survives the account switch that causes it', () async {
      // The guest's PushRegistration is discarded when the account services are
      // built, so the pending id has to outlive the object that stored it.
      final guestPush = await started(guest: true);
      messaging.opened.add(weekly);
      await pumpEventQueue();
      await guestPush.stop();
      await guestPush.dispose();
      expect(PushRegistration.pendingTap, snapshotId);
    });
  });

  // --- 6. after signing out, taps stop ------------------------------------

  test('a tap after stop is ignored', () async {
    final push = await started();
    await push.stop();
    messaging.opened.add(weekly);
    await pumpEventQueue();
    expect(opened, isEmpty);
  });

  // --- 7. the screen is the only thing that decides what to show ----------

  //
  // Everything above proves the tap reaches this screen with the right id. What
  // the screen then shows is decided by the server, because the payload has no
  // account in it: `GET /snapshots/{id}` answers 404 for a snapshot that
  // belongs to somebody else or has been deleted, and the screen has to show
  // its unavailable state rather than anything it might guess.
  group('ownership is decided by the server, not the payload', () {
    /// Services whose snapshot GET is answered by [respond].
    Future<AppServices> servicesFor(WidgetTester tester, _Responder respond) async {
      final db = (await tester.runAsync(
          () => LocalDb.openAt(inMemoryDatabasePath)))!;
      final dio = Dio(BaseOptions(
          validateStatus: (status) => status != null && status < 600))
        ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) async {
          handler.resolve(await respond(request));
        }));
      return AppServices(
        db: db,
        api: ApiClient(dio: dio, tokens: _Tokens()),
        guest: true,
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
        sync: SyncService(
          pending: () async => [],
          send: (_) async => throw UnimplementedError(),
          apply: (_) async {},
          markFailed: (_) async {},
          isOnline: () async => false,
        ),
      );
    }

    /// Let the page's own async load settle, the way the other UI tests do.
    Future<void> settle(WidgetTester tester) async {
      for (var i = 0; i < 10; i++) {
        await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 5)));
        await tester.pump();
      }
      await tester.pumpAndSettle();
    }

    testWidgets('a snapshot belonging to another account shows the '
        'unavailable state and no content', (tester) async {
      final services = await servicesFor(tester, (request) async {
        // Exactly what the backend answers for another user's snapshot id.
        return Response(
            requestOptions: request,
            statusCode: 404,
            data: {'detail': 'not found'});
      });

      await tester.pumpWidget(MaterialApp(
        home: WorthAnotherLookPage(snapshotId: snapshotId, services: services),
      ));
      await settle(tester);

      expect(find.text('This list is unavailable right now.'), findsOneWidget);
      expect(find.byType(ResultCard), findsNothing);
      expect(find.textContaining('Sourdough'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.runAsync(() => services.dispose());
    });

    testWidgets('the tap reaches the screen with exactly the id from the '
        'payload', (tester) async {
      final requested = <String>[];
      final services = await servicesFor(tester, (request) async {
        requested.add(request.uri.path);
        return Response(
            requestOptions: request,
            statusCode: 200,
            data: {
              'snapshot_id': snapshotId,
              'original_count': 1,
              'available_count': 1,
              'items': [
                {
                  'id': 'a',
                  'url': 'https://example.test/a',
                  'title': memoryTitle,
                  'title_clean': memoryTitle,
                  'category': 'other',
                  'status': 'ready',
                  'tags': <String>[],
                }
              ],
            });
      });

      await tester.pumpWidget(MaterialApp(
        home: WorthAnotherLookPage(snapshotId: snapshotId, services: services),
      ));
      await settle(tester);

      expect(requested, ['/api/v1/snapshots/$snapshotId']);
      expect(find.byType(ResultCard), findsOneWidget);
      expect(find.text(memoryTitle), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      await tester.runAsync(() => services.dispose());
    });
  });

  // --- 6. a note for another account --------------------------------------

  // The payload carries the account it belongs to. When that is not the account
  // signed in here, the note is not opened and nothing is fetched: this is what
  // a device token that changed hands looks like from the app's side. The
  // server sends to whoever owns the token now, and the note is really about
  // the previous account.
  group('note addressed to another account', () {
    const mine = 'account-mine';
    const theirs = 'account-theirs';

    PushPayload forThem([String snapshot = snapshotId]) => PushPayload(
        type: 'weekly_note', snapshotId: snapshot, accountId: theirs);

    PushPayload forMe([String snapshot = snapshotId]) => PushPayload(
        type: 'weekly_note', snapshotId: snapshot, accountId: mine);

    test('a background tap for another account is ignored', () async {
      await started(accountId: mine);
      messaging.opened.add(forThem());
      await pumpEventQueue();

      expect(opened, isEmpty,
          reason: 'a note for somebody else must not open a screen');
      expect(PushRegistration.pendingTap, isNull,
          reason: 'nor is it held for replay after signing in');
    });

    test('a terminated launch for another account is ignored', () async {
      // The tap is what launched the app, so this is the read-not-a-stream
      // path rather than the subscription above.
      messaging.initial = forThem();
      await started(accountId: mine);
      await pumpEventQueue();

      expect(opened, isEmpty);
      expect(PushRegistration.pendingTap, isNull);
    });

    test('the user is sent home rather than left where they were', () async {
      final push = await started(accountId: mine);
      var sentHome = 0;
      push.onForeignTap = () => sentHome++;

      messaging.opened.add(forThem());
      await pumpEventQueue();

      expect(sentHome, 1,
          reason: 'the app pops to the library so the tap is not a silent no-op');
    });

    test('no snapshot request is made for another account', () async {
      // Nothing is fetched, so another person\'s snapshot id is never even
      // sent to the server from this device.
      final push = await started(accountId: mine);
      var requests = 0;
      push.onForeignTap = () {};

      messaging.opened.add(forThem());
      await pumpEventQueue();

      expect(opened, isEmpty);
      expect(requests, 0);
    });

    test('a note for the signed-in account still opens', () async {
      await started(accountId: mine);
      messaging.opened.add(forMe());
      await pumpEventQueue();

      expect(opened, [snapshotId]);
    });

    test('a payload with no account id still opens', () async {
      // Older servers sent only type and snapshot_id. Refusing those would
      // lock the user out of their own note, so an unknown sender passes and
      // the snapshot endpoint remains the thing that decides ownership.
      await started(accountId: mine);
      messaging.opened.add(weekly);
      await pumpEventQueue();

      expect(opened, [snapshotId]);
    });

    test('a guest holds the note for replay instead of refusing it', () async {
      // accountId is null exactly when the scope is a guest. The guard must not
      // fail closed here: that would discard the tap rather than hold it, and
      // the user who signs in to that account would get nothing. The guest
      // branch runs next and holds it, and the fresh registration built after
      // sign-in re-runs this comparison with an account id to match.
      await started(guest: true, accountId: null);
      messaging.opened.add(forThem());
      await pumpEventQueue();

      expect(opened, isEmpty);
      expect(PushRegistration.pendingTap, snapshotId,
          reason: 'held for replay, not dropped');
    });

    test('the held tap remembers which account it belongs to', () async {
      // A guest scope cannot run the comparison, so the note's account has to
      // survive until sign-in -- otherwise the replay has nothing to check and
      // the guard is silently skipped for exactly the taps that need it.
      await started(guest: true, accountId: null);
      messaging.opened.add(forThem());
      await pumpEventQueue();

      expect(PushRegistration.pendingTapAccount, theirs);
      expect(PushRegistration.pendingTap, snapshotId);

      PushRegistration.takePendingTap();
      expect(PushRegistration.pendingTapAccount, isNull,
          reason: 'taking the tap clears the account with it');
    });

    test('a held note for another account is refused at replay', () async {
      // The guest scope could not make this comparison, so it happens when the
      // tap is replayed. Without this the guard would be skipped for exactly
      // the taps that arrive while signed out.
      await started(guest: true, accountId: null);
      messaging.opened.add(forThem());
      await pumpEventQueue();
      expect(PushRegistration.pendingTap, snapshotId, reason: 'held first');

      final replay = PushRegistration.replayPendingTapFor(mine);

      expect(replay.snapshotId, isNull, reason: 'nothing may be opened');
      expect(replay.refused, isTrue,
          reason: 'the caller sends the user home for a refused tap');
    });

    test('a held note for the signed-in account is replayed', () async {
      await started(guest: true, accountId: null);
      messaging.opened.add(forMe());
      await pumpEventQueue();

      final replay = PushRegistration.replayPendingTapFor(mine);

      expect(replay.snapshotId, snapshotId);
      expect(replay.refused, isFalse);
    });

    test('nothing held is not a refusal', () async {
      // Without this the caller cannot tell "no tap" from "refused tap", and
      // would bounce the user home on every sign-in.
      final replay = PushRegistration.replayPendingTapFor(mine);

      expect(replay.snapshotId, isNull);
      expect(replay.refused, isFalse);
    });

    test('a refused replay cannot be retried against another account', () async {
      await started(guest: true, accountId: null);
      messaging.opened.add(forThem());
      await pumpEventQueue();

      PushRegistration.replayPendingTapFor(mine);

      final second = PushRegistration.replayPendingTapFor(theirs);
      expect(second.snapshotId, isNull,
          reason: 'the tap was consumed by the refusal');
    });

    test('the guard survives a token refresh', () async {
      // A refresh replaces the token, not the account, so the comparison must
      // still hold afterwards.
      await started(accountId: mine);
      messaging.refreshes.add('token-b');
      await pumpEventQueue();

      messaging.opened.add(forThem());
      await pumpEventQueue();
      expect(opened, isEmpty);

      messaging.opened.add(forMe());
      await pumpEventQueue();
      expect(opened, [snapshotId]);
    });
  });
}
