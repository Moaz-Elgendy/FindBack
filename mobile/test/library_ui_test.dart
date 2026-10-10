import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/features/home/home_screen.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => null;
}

Map<String, dynamic> _item(String id, String category) => {
  'id': id, 'url': 'https://example.test/$id', 'title_clean': '$id title',
  'category': category, 'status': 'ready', 'tags': <String>[],
};


void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() { sqfliteFfiInit(); databaseFactory = databaseFactoryFfi;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });

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

  Future<AppServices> setup(WidgetTester tester, {
    required RemoteRecentFetch recent, bool duplicate = false, SyncService? syncService, ApiClient? searchApi,
  }) async {
    final db = (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    final api = searchApi ?? ApiClient(tokens: _Tokens());
    return AppServices(db: db, api: api,
      items: ItemsService(remoteItem: (_) async => ItemDetail.fromJson({
        ..._item('old', 'tutorial'), 'instant_brief': 'Existing useful Brief.'}),
        localItem: (_) async => null, remoteRecent: recent,
        localRecent: (_, {String? category, Map<String, String>? filters}) async => [], cache: (_) async {},
        remoteDelete: (_) async {}, localDelete: (_) async => 0,
        dropQueued: (_) async => 0, isOnline: () async => true),
      capture: CaptureService(ingest: (_, __, ___) async => IngestResult(
          id: 'old', status: 'ready', canonicalUrl: 'https://example.test/old',
          alreadyExists: duplicate),
        queue: (_, __, ___) async => throw AssertionError('not offline'),
        isOnline: () async => true),
      share: ShareIntentService(),
      sync: syncService ?? SyncService(pending: () async => [], send: (_) async => throw UnimplementedError(),
        apply: (_) async {}, markFailed: (_) async => {}, isOnline: () async => false));
  }


  testWidgets('completed local imports remain visible while the account feed is empty', (tester) async {
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async => const ItemPage(items: []));
    await tester.runAsync(() async {
      final client = await services.db.queueSave(url: 'https://example.test/imported');
      await services.db.upsertRemoteItems([ItemDetail.fromJson({
        ..._item('local-$client', 'tutorial'), 'title_clean': 'Completed imported memory',
        'instant_brief': 'Original completed brief', 'brief_source': 'llm',
      })]);
    });
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    for (var i = 0; i < 20; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
      await tester.pump();
    }
    expect(find.text('Completed imported memory'), findsOneWidget);
    expect(find.text('Reading 1'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('processing status replaces refresh and clears automatically', (tester) async {
    var finalized = false;
    var requests = 0;
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async {
        requests++;
        return ItemPage(items: [ItemDetail.fromJson({..._item('one', 'tutorial'),
          'status': finalized ? 'ready' : 'processing',
          if (finalized) 'brief_source': 'llm',
          if (finalized) 'instant_brief': 'Final useful Brief.'})]);
    });
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.byTooltip('Refresh'), findsNothing);
    expect(find.text('Reading 1'), findsOneWidget);
    expect(find.text('Pull down to refresh'), findsNothing);
    final list = tester.widget<ListView>(find.byType(ListView).last);
    expect(list.physics, isA<AlwaysScrollableScrollPhysics>());
    finalized = true;
    await tester.pump(const Duration(seconds: 5));
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });
    await tester.pumpAndSettle();
    expect(find.text('Reading 1'), findsNothing);
    expect(find.byKey(const ValueKey('processing-border')), findsNothing);
    final afterFinal = requests;
    await tester.pump(const Duration(seconds: 10));
    expect(requests, afterFinal, reason: 'Polling stops after finalization');
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('processing polls back off while its status is unchanged', (tester) async {
    var requests = 0;
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async {
      requests++;
      return ItemPage(items: [ItemDetail.fromJson({'id': 'server-reading', 'url': 'https://example.com/reading', 'status': 'pending'})]);
    });
    Future<void> drain() async {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
      await tester.pump(const Duration(milliseconds: 100));
    }
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await drain();
    final initial = requests;
    await tester.pump(const Duration(seconds: 5)); await drain();
    final first = requests;
    expect(first, greaterThan(initial));
    await tester.pump(const Duration(seconds: 5)); await drain();
    expect(requests, first);
    await tester.pump(const Duration(seconds: 5)); await drain();
    expect(requests, greaterThan(first));
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('local queued cards do not poll the server feed', (tester) async {
    var requests = 0;
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async {
      requests++;
      return ItemPage(items: [ItemDetail.fromJson({'id': 'local-queued', 'url': 'https://example.com/queued', 'title': 'Queued', 'status': 'pending'})]);
    });
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    for (var i = 0; i < 10; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
      await tester.pump(const Duration(milliseconds: 100));
    }
    final before = requests;
    await tester.pump(const Duration(seconds: 20));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pump();
    expect(requests, before);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('offline queue and local memory are counted once', (tester) async {
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async =>
        ItemPage(items: [
          ItemDetail.fromJson({..._item('local-one', 'tutorial'), 'status': 'pending'}),
          ItemDetail.fromJson({..._item('server', 'tutorial'), 'status': 'processing'}),
          ItemDetail.fromJson({..._item('finished', 'tutorial'), 'status': 'processing',
            'needs_retry': true, 'brief_source': 'llm', 'instant_brief': 'Final Brief.'}),
        ]));
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Reading 2'), findsOneWidget);
    expect(find.byKey(const ValueKey('processing-border')), findsNWidgets(2));
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('first queued save appears even when server library is empty', (tester) async {
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async => const ItemPage(items: []));
    await tester.runAsync(() => services.db.queueSave(url: 'https://example.test/queued', titleHint: 'Queued memory'));
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    for (var i = 0; i < 20; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
      await tester.pump();
    }
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Reading 1'), findsOneWidget);
    expect(find.text('Saved on this phone'), findsOneWidget);
    expect(find.text('Save your first link'), findsNothing);
    expect(find.text('Nothing matches yet'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('empty library refreshes silently when the app returns', (tester) async {
    var requests = 0;
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async {
      requests++;
      return const ItemPage(items: []);
    });
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    expect(find.text('Save your first link'), findsOneWidget);
    expect(find.text('Pull down to refresh'), findsNothing);
    final before = requests;
    expect(find.byType(RefreshIndicator), findsNothing);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.runAsync(() async {
      for (var i = 0; i < 20 && requests <= before; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
    });
    await tester.pumpAndSettle();
    expect(requests, greaterThan(before));
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('topic selector hides generated phrases and refreshes its groups', (tester) async {
    final records = [
      ItemDetail.fromJson({..._item('one', 'tutorial'), 'title_clean': 'Claude skills', 'topics': ['AI']}),
      ItemDetail.fromJson({..._item('two', 'tutorial'), 'title_clean': 'Claude extensions', 'topics': ['AI']}),
      ItemDetail.fromJson({..._item('three', 'tutorial'), 'topics': ['AI']}),
      ItemDetail.fromJson({..._item('food', 'recipe'), 'topics': ['Food']}),
    ];
    final services = await setup(tester, recent: (limit, {category, cursor, filters}) async =>
        ItemPage(items: records.where((item) => matchesIntelligence(SearchResult.fromItem(item), filters ?? {})).toList()));
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services, findMode: true)));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(FilterChip, 'about AI'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'about Food'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'Claude Code skills'), findsNothing);
    await tester.tap(find.text('More filters'));
    await tester.pumpAndSettle();
    final topic = tester.widget<DropdownButtonFormField<String>>(find.byType(DropdownButtonFormField<String>).first);
    final dropdown = find.descendant(of: find.byWidget(topic), matching: find.byType(DropdownButton<String>));
    expect(tester.widget<DropdownButton<String>>(dropdown).items!.map((item) => item.value), [null, 'AI', 'Food']);
    Navigator.of(tester.element(find.text('More filters').last)).pop();
    await tester.pumpAndSettle();
    records.removeWhere((item) => item.id == 'food');
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(FilterChip, 'about Food'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('topic filters remain readable and have 48px targets at double text size', (tester) async {
    tester.view.physicalSize = const Size(320, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final services = await setup(tester,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async =>
        ItemPage(items: [ItemDetail.fromJson({..._item('readable', 'tutorial'), 'topics': ['AI']})]));
    await tester.pumpWidget(MaterialApp(
      theme: ThemeData(useMaterial3: true, brightness: Brightness.dark),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!),
      home: HomeScreen(services: services, findMode: true)));
    await tester.pumpAndSettle();
    final chips = find.widgetWithText(FilterChip, 'about AI');
    expect(tester.getSize(chips).height, greaterThanOrEqualTo(44));
    expect(tester.getSize(find.widgetWithText(TextButton, 'More filters')).height, greaterThanOrEqualTo(44));
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });
  testWidgets('topic beyond page 20 and content-type sheet filter reach the whole library', (tester) async {
    const connectivity = MethodChannel('dev.fluttercommunity.plus/connectivity');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(connectivity, (_) async => ['wifi']);
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(connectivity, null));
    final requested = <Map<String, String>>[];
    final records = [for (var i = 0; i < 25; i++) ItemDetail.fromJson({
      ..._item('$i', 'tutorial'), 'topics': [i == 24 ? 'AI' : 'Food'],
      'content_type': i == 24 ? 'ai_tool' : 'recipe',
      'entities': {'tools_products': [i == 24 ? 'Claude' : 'Oven']},
    })];
    final dio = Dio()..interceptors.add(InterceptorsWrapper(onRequest: (options, handler) => handler.resolve(
      Response(requestOptions: options, statusCode: 200, data: {'results': [], 'took_ms': 0}))));
    final services = await setup(tester, searchApi: ApiClient(dio: dio, tokens: _Tokens()), recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async {
      requested.add(Map.of(filters ?? {}));
      final matching = records.where((item) => matchesIntelligence(SearchResult.fromItem(item), filters ?? {})).toList();
      final start = cursor == null ? 0 : int.parse(cursor);
      return ItemPage(items: matching.skip(start).take(limit).toList(),
        nextCursor: start + limit < matching.length ? '${start + limit}' : null);
    });
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services, findMode: true)));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilterChip, 'about AI'));
    await tester.pumpAndSettle();
    expect(find.text('24 title'), findsOneWidget);
    expect(find.text('0 title'), findsNothing);
    await tester.tap(find.text('More filters (1)'));
    await tester.pumpAndSettle();
    expect(find.text('More filters'), findsOneWidget);
    await tester.tap(find.byType(DropdownButtonFormField<String>).at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('ai tool').last);
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Apply filters'), 200, scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('Apply filters'));
    await tester.pumpAndSettle();
    expect(requested.last, {'topic': 'AI', 'type': 'ai_tool'});
    expect(find.text('24 title'), findsOneWidget);
    expect(find.text('More filters (2)'), findsOneWidget);
    await tester.enterText(find.byType(TextField).first, 'Claude');
    await tester.pump(const Duration(milliseconds: 300));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('More filters (2)'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Clear filters'), 200, scrollable: find.byType(Scrollable).last);
    await tester.tap(find.text('Clear filters'));
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.close));
    await tester.pumpAndSettle();
    expect(requested.last, isEmpty);
    expect(find.text('0 title'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('library loads items past 20 and stops at the last page', (tester) async {
    final asked = <String?>[];
    final services = await setup(tester, recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async {
      if (limit == HomeScreen.recentLimit) asked.add(cursor);
      final start = cursor == null ? 0 : int.parse(cursor);
      final end = start + limit > 45 ? 45 : start + limit;
      return ItemPage(items: [for (var i = start; i < end; i++) ItemDetail.fromJson(_item('$i', 'tutorial'))],
          nextCursor: end < 45 ? '$end' : null);
    });
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    for (var i = 0; i < 2; i++) {
      await tester.scrollUntilVisible(find.text('Load more').hitTestable(), 400, scrollable: find.byType(Scrollable).last);
      await tester.tap(find.text('Load more'));
      await tester.pumpAndSettle();
    }
    await tester.scrollUntilVisible(find.text('44 title').hitTestable(), 400, scrollable: find.byType(Scrollable).last);
    expect(find.text('44 title'), findsOneWidget);
    expect(find.text('Load more'), findsNothing);
    expect(asked, [null, '20', '40']);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('duplicate outside loaded page shows Already saved toast with View', (tester) async {
    final services = await setup(tester, duplicate: true,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async => ItemPage(
        items: [ItemDetail.fromJson(_item('new', 'tutorial'))]));
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Save a link'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, 'https://example.test/old');
    await tester.tap(find.widgetWithText(FilledButton, 'Save'));
    await tester.pumpAndSettle();
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(find.text('Already saved'), findsOneWidget);
    expect(find.text('View'), findsOneWidget);
    expect(find.byType(AlertDialog), findsNothing);
    expect(find.text('Saved. Reading it now.'), findsNothing);
    await tester.tap(find.text('View'));
    await tester.pumpAndSettle();
    expect(find.text('Existing useful Brief.'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });
  testWidgets('repeat from phone sharing uses the same Already saved toast', (tester) async {
    final services = await setup(tester,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []));
    services.sharedCapture.value = const CaptureBatch([CaptureOutcome(status: CaptureStatus.remote,
        reference: 'old', alreadyExists: true)]);
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(find.text('Already saved'), findsOneWidget);
    expect(services.sharedCapture.value, isNull);
    await tester.tap(find.text('View'));
    await tester.pumpAndSettle();
    expect(find.text('Existing useful Brief.'), findsOneWidget);
    expect(find.text('Open original'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('failed uploads explain the connection and retry on resume', (tester) async {
    var reachable = false;
    final queue = [const SyncItem(clientId: 'q', url: 'https://example.test/q', capturedAt: 'now')];
    final sync = SyncService(pending: () async => queue, send: (_) async {
      if (!reachable) throw ApiException('connection refused');
      return const SyncBatchResult(mapped: [MappedSave(clientId: 'q', serverId: 'server')], failedClientIds: []);
    }, apply: (_) async => queue.clear(), markFailed: (_) async {},
      isOnline: () async => true, retryBase: const Duration(hours: 1));
    await sync.flush();
    final services = await setup(tester, syncService: sync,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []));
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    expect(find.text('Cannot reach the processing service. Your links are safe and will retry.'), findsOneWidget);
    reachable = true;
    expect(find.text('Retry upload'), findsNothing);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(queue, isEmpty);
    expect(sync.lastError.value, isNull);
    expect(find.text('Cannot reach the processing service. Your links are safe and will retry.'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('successful sync replaces the queued card without manual refresh', (tester) async {
    var synced = false;
    final services = await setup(tester,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async => ItemPage(items: [
        ItemDetail.fromJson(_item(synced ? 'server' : 'local-1', 'tutorial'))]));
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    expect(find.text('local-1 title'), findsOneWidget);
    synced = true;
    services.pending.value = 0;
    await tester.pumpAndSettle();
    expect(find.text('server title'), findsOneWidget);
    expect(find.text('local-1 title'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

  testWidgets('resume retries queued uploads after the backend returns', (tester) async {
    var reachable = false;
    var sends = 0;
    final queue = [const SyncItem(clientId: 'q', url: 'https://example.test/q', capturedAt: 'now')];
    final sync = SyncService(pending: () async => queue, send: (_) async {
      sends++;
      if (!reachable) throw ApiException('connection refused');
      return const SyncBatchResult(mapped: [MappedSave(clientId: 'q', serverId: 'server')], failedClientIds: []);
    }, apply: (_) async => queue.clear(), markFailed: (_) async {},
      isOnline: () async => true, retryBase: const Duration(hours: 1));
    await sync.flush();
    final services = await setup(tester, syncService: sync,
      recent: (limit, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []));
    services.pending.value = 1;
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    reachable = true;
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 100)));
    await tester.pumpAndSettle();
    expect(sends, 2);
    expect(queue, isEmpty);
    await tester.pumpWidget(const SizedBox());
    await drive(tester, services.dispose());
  });

}
