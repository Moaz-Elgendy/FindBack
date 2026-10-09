/// The weekly note's "Worth another look" screen.
///
/// The three states the screen can be in — loading, empty/unavailable and the
/// normal card list — plus the two guarantees around them: it asks the server
/// for exactly `GET /snapshots/{id}`, and the back button is its only control.
/// The normal state is also driven in both themes, in RTL, at a 2.0 text
/// scale, because dark mode and large text are part of this addendum's
/// definition of done.
library;

import 'dart:async';

import 'package:dio/dio.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/features/weekly_note/worth_another_look_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => null;
}

typedef _Responder = FutureOr<Response<dynamic>> Function(RequestOptions request);

Map<String, dynamic> _item(String id, String title) => <String, dynamic>{
      'id': id,
      'url': 'https://example.test/$id',
      'title_clean': title,
      'title': title,
      'category': 'other',
      'status': 'ready',
      'created_at': '2026-08-01T10:00:00Z',
      'tags': <String>[],
    };

Response<dynamic> _ok(RequestOptions request, Object? body) =>
    Response(requestOptions: request, statusCode: 200, data: body);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
            const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });

  /// Lets futures that run outside the fake clock (SQLite, platform channels)
  /// finish, the way the other UI tests drain them.
  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 20; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    await tester.pumpAndSettle();
  }

  Future<void> drive(WidgetTester tester, Future<void> work) async {
    var done = false;
    final closing = work.then((_) => done = true);
    for (var i = 0; i < 100 && !done; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(done, isTrue, reason: 'Services close after platform and SQLite callbacks');
    await closing;
  }

  /// Services whose snapshot GET is answered by [respond].
  Future<AppServices> servicesFor(WidgetTester tester, _Responder respond) async {
    final db = (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    final dio = Dio(BaseOptions(validateStatus: (status) => status != null && status < 600))
      ..interceptors.add(InterceptorsWrapper(onRequest: (request, handler) async {
        handler.resolve(await respond(request));
      }));
    final api = ApiClient(dio: dio, tokens: _Tokens());
    return AppServices(
      db: db,
      api: api,
      guest: true,
      items: ItemsService(
        remoteItem: (_) async => throw UnimplementedError(),
        localItem: (_) async => null,
        remoteRecent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async =>
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

  testWidgets('loading shows a spinner, then the snapshot cards newest first', (tester) async {
    final gate = Completer<Object?>();
    final requested = <String>[];
    final services = await servicesFor(tester, (request) async {
      requested.add(request.uri.path);
      return _ok(request, await gate.future);
    });
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.byType(ResultCard), findsNothing);

    gate.complete(<String, Object?>{
      'items': [
        _item('newest', 'Sourdough basics'),
        _item('older', 'Garden plan for spring'),
      ],
    });
    await settle(tester);

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(requested, ['/api/v1/snapshots/snap-7']);
    final titles = tester
        .widgetList<ResultCard>(find.byType(ResultCard))
        .map((card) => card.result.title)
        .toList();
    expect(titles, ['Sourdough basics', 'Garden plan for spring']);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('an empty snapshot shows the plain empty notice', (tester) async {
    final services =
        await servicesFor(tester, (request) async => _ok(request, <String, Object?>{'items': []}));
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    await settle(tester);
    expect(find.text('Nothing to look back on.'), findsOneWidget);
    expect(find.byType(ResultCard), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  Future<void> expectUnavailable(WidgetTester tester, _Responder respond) async {
    final services = await servicesFor(tester, respond);
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    await settle(tester);
    expect(find.text('This list is unavailable right now.'), findsOneWidget);
    expect(find.byType(ResultCard), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  }

  testWidgets('a missing snapshot shows the unavailable state', (tester) async => expectUnavailable(
        tester,
        (request) => Response(
            requestOptions: request, statusCode: 404, data: {'detail': 'no such snapshot'})));

  testWidgets('a failed connection shows the unavailable state', (tester) async => expectUnavailable(
      tester,
      (request) async =>
          throw DioException(requestOptions: request, type: DioExceptionType.connectionError)));

  testWidgets('the normal state is the card list with back as the only control', (tester) async {
    final services = await servicesFor(tester, (request) async => _ok(request, <String, Object?>{
      'items': [
        _item('a', 'Sourdough basics'),
        _item('b', 'Garden plan for spring'),
      ],
    }));
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(MaterialApp(
      navigatorKey: navKey,
      home: const Scaffold(body: Text('Library')),
    ));
    navKey.currentState!.push(MaterialPageRoute<void>(
        builder: (_) => WorthAnotherLookPage(snapshotId: 'snap-7', services: services)));
    await settle(tester);

    expect(find.text('Worth another look'), findsOneWidget);
    expect(find.byType(ResultCard), findsNWidgets(2));
    expect(find.byType(BackButton), findsOneWidget);
    // Back button only: no menus, chips, dropdowns, refresh or other controls.
    expect(find.byType(IconButton), findsOneWidget);
    expect(find.byType(PopupMenuButton), findsNothing);
    expect(find.byType(FilterChip), findsNothing);
    expect(find.byType(DropdownButton), findsNothing);
    expect(find.byType(RefreshIndicator), findsNothing);

    await tester.tap(find.byType(BackButton));
    await tester.pumpAndSettle();
    expect(find.text('Library'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  for (final brightness in Brightness.values) {
    testWidgets('normal state in ${brightness.name} theme, RTL and 2.0 text scale', (tester) async {
      tester.view.physicalSize = const Size(320, 740);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final services = await servicesFor(tester, (request) async => _ok(request, <String, Object?>{
        'items': [
          _item('a', 'Sourdough basics'),
          _item('b', 'A deliberately long forgotten title that has to wrap onto several lines '
              'once the text scale is doubled'),
        ],
      }));
      await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(brightness),
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)),
          child: Directionality(textDirection: TextDirection.rtl, child: child!),
        ),
        home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
      ));
      await settle(tester);

      expect(find.byType(ResultCard), findsNWidgets(2));
      // Measured on a body widget, not the AppBar title: the framework clamps
      // title text to 1.34x on purpose, the body keeps the full scale.
      final context = tester.element(find.byType(ResultCard).first);
      expect(Directionality.of(context), TextDirection.rtl);
      expect(MediaQuery.textScalerOf(context).scale(14), 28);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await drive(tester, services.dispose());
    });
  }

  // --- saves deleted after the note -------------------------------------
  //
  // The notification promised a count and the server sends back both numbers,
  // because a save deleted since the note was sent is dropped from `items`.
  // Showing the shorter list silently would read as a bug; the screen has to
  // reconcile the two numbers itself.

  Map<String, Object?> shrunk(int available, int original) => <String, Object?>{
        'snapshot_id': 'snap-7',
        'created_at': '2026-10-04T18:00:00Z',
        'original_count': original,
        'available_count': available,
        'items': <Map<String, Object?>>[
          for (var i = 0; i < available; i++) _item('id-$i', 'Sourdough basics $i'),
        ],
      };

  testWidgets('a snapshot missing saves says so, with both counts', (tester) async {
    final services =
        await servicesFor(tester, (request) async => _ok(request, shrunk(2, 3)));
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    await settle(tester);

    expect(find.text('Showing 2 of 3 — some saves are no longer available.'),
        findsOneWidget);
    expect(find.byType(ResultCard), findsNWidgets(2));
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('a complete snapshot shows no such notice', (tester) async {
    final services =
        await servicesFor(tester, (request) async => _ok(request, shrunk(2, 2)));
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    await settle(tester);

    expect(find.textContaining('no longer available'), findsNothing);
    expect(find.byType(ResultCard), findsNWidgets(2));
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('every save gone is still an honest list, not a crash', (tester) async {
    /// 0 available of 3: the empty notice and the shortfall notice must not
    /// both appear, and neither may overflow.
    final services =
        await servicesFor(tester, (request) async => _ok(request, shrunk(0, 3)));
    await tester.pumpWidget(MaterialApp(
      home: WorthAnotherLookPage(snapshotId: 'snap-7', services: services),
    ));
    await settle(tester);

    expect(find.byType(ResultCard), findsNothing);
    expect(find.textContaining('no longer available'), findsNothing);
    expect(find.text('Nothing to look back on.'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('the shortfall notice adds no controls and survives a large scale',
      (tester) async {
    tester.view.physicalSize = const Size(320, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final services =
        await servicesFor(tester, (request) async => _ok(request, shrunk(2, 3)));
    // Pushed rather than set as `home:`, so the AppBar's automatic back button
    // exists and "no other controls" is a meaningful claim.
    final navKey = GlobalKey<NavigatorState>();
    await tester.pumpWidget(MaterialApp(
      navigatorKey: navKey,
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)),
        child: Directionality(textDirection: TextDirection.rtl, child: child!),
      ),
      home: const Scaffold(body: Text('Library')),
    ));
    navKey.currentState!.push(MaterialPageRoute<void>(
        builder: (_) =>
            WorthAnotherLookPage(snapshotId: 'snap-7', services: services)));
    await settle(tester);

    expect(find.textContaining('no longer available'), findsOneWidget);
    expect(find.byType(ResultCard), findsNWidgets(2));
    // Back is still the only control the screen offers.
    expect(find.byType(IconButton), findsOneWidget);
    expect(find.byType(PopupMenuButton), findsNothing);
    expect(find.byType(FilterChip), findsNothing);
    expect(find.byType(DropdownButton), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  test('SnapshotPage parses the counts and the shortfall', () {
    final page = SnapshotPage.fromJson(shrunk(2, 3));
    expect(page.snapshotId, 'snap-7');
    expect(page.createdAt, isNotNull);
    expect(page.items, hasLength(2));
    expect(page.originalCount, 3);
    expect(page.availableCount, 2);
    expect(page.hasUnavailable, isTrue);
  });

  test('SnapshotPage falls back to the list length when counts are absent', () {
    /// An older server that sends only `items` must not claim saves are gone.
    final page = SnapshotPage.fromJson(<String, Object?>{
      'items': <Map<String, Object?>>[_item('a', 'Sourdough basics')],
    });
    expect(page.originalCount, 1);
    expect(page.availableCount, 1);
    expect(page.hasUnavailable, isFalse);
  });
}
