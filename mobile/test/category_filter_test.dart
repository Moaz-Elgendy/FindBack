import 'dart:convert';
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/data/token_store.dart';
import 'package:findback/features/home/home_screen.dart';
import 'package:findback/features/home/search_controller.dart' as search;
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

class _Tokens extends TokenStore {
  @override
  Future<String?> read() async => null;
}

Map<String, dynamic> _item(String id, String category) => {
  'id': id, 'url': 'https://example.test/$id', 'title_clean': '$id title',
  'category': category, 'status': 'ready', 'tags': <String>[],
  'topics': [category == 'recipe' ? 'Food' : category == 'tutorial' ? 'Gym' : 'AI'], 'content_type': category,
};

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });

  test('mobile category asset matches the backend category list', () async {
    final canonical = jsonDecode(await File('../backend/app/categories.json').readAsString());
    final bundled = jsonDecode(await File('assets/categories.json').readAsString());
    expect(bundled, canonical);
    expect(bundled, isNotEmpty);
  });

  test('list pagination retains the selected category in the request', () async {
    final asked = <Map<String, dynamic>>[];
    final dio = Dio()..interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        asked.add(Map.of(options.uri.queryParameters));
        handler.resolve(Response(requestOptions: options, statusCode: 200,
          data: {'items': [_item('recipe', 'recipe')], 'next_cursor': 'next'}));
      },
    ));
    final api = ApiClient(dio: dio, tokens: _Tokens());
    final page = await api.listItems(category: 'recipe');
    await api.listItems(category: 'recipe', cursor: page.nextCursor);
    expect(asked[0]['category'], 'recipe');
    expect(asked[1]['category'], 'recipe');
    expect(asked[1]['cursor'], 'next');
    await api.listItems(category: 'All');
    expect(asked.last.containsKey('category'), isFalse);
    api.close();
  });

  for (final online in [false, true]) {
    test('category survives ${online ? "API error" : "offline"} fallbacks', () async {
      final db = await LocalDb.openAt(inMemoryDatabasePath);
      final rows = [_item('recipe', 'recipe'), _item('article', 'article')];
      await db.upsertRemoteItems(rows.map(ItemDetail.fromJson).toList());
      final dio = Dio()..interceptors.add(InterceptorsWrapper(
        onRequest: (options, handler) => handler.resolve(Response(
          requestOptions: options, statusCode: 503, data: {'detail': 'unavailable'})),
      ));
      final api = ApiClient(dio: dio, tokens: _Tokens());
      final items = ItemsService.of(api: api, db: db, isOnline: () async => online);
      final controller = search.SearchController.of(
        api: api, local: db.localSearch, isOnline: () async => online);
      try {
        expect((await items.recent(category: 'recipe')).map((r) => r.id), ['recipe']);
        await controller.run('title', 'recipe');
        expect(controller.results.map((r) => r.id), ['recipe']);
        expect(controller.offline, isTrue);
      } finally {
        controller.dispose();
        api.close();
        await db.close();
      }
    });
  }

  testWidgets('topic-filtered empty-query chip tap changes the visible Recent list', (tester) async {
    final db = (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    final asked = <String?>[];
    final dio = Dio()..interceptors.add(InterceptorsWrapper(
      onRequest: (options, handler) {
        final filters = jsonDecode(options.uri.queryParameters['intelligence'] ?? '{}') as Map<String, dynamic>;
        asked.add(filters['topic'] as String?);
        final rows = [_item('article', 'article'), _item('recipe', 'recipe'), _item('tutorial', 'tutorial')];
        handler.resolve(Response(requestOptions: options, statusCode: 200,
          data: {'items': rows.where((row) => filters['topic'] == null || (row['topics'] as List).contains(filters['topic'])).toList()}));
      },
    ));
    final api = ApiClient(dio: dio, tokens: _Tokens());
    final services = AppServices(db: db, api: api,
      items: ItemsService(remoteItem: api.getItem, localItem: (_) async => null,
        remoteRecent: (limit, {String? category, String? cursor, Map<String, String>? filters}) =>
            api.listItems(limit: limit, category: category, cursor: cursor, filters: filters),
        localRecent: (_, {String? category, Map<String, String>? filters}) async => [], cache: (_) async {},
        remoteDelete: api.deleteItem, localDelete: (_) async => 0,
        dropQueued: (_) async => 0, isOnline: () async => true),
      capture: CaptureService.of(db: db, api: api),
      share: ShareIntentService(),
      sync: SyncService(pending: () async => [], send: (_) async => throw UnimplementedError(),
        apply: (_) async {}, markFailed: (_) async {}, isOnline: () async => false));
    await tester.pumpWidget(MaterialApp(home: HomeScreen(services: services)));
    await tester.pumpAndSettle();
    expect(find.text('article title'), findsOneWidget);
    expect(find.text('recipe title'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilterChip, 'Food'));
    await tester.pumpAndSettle();
    expect(asked.last, 'Food');
    expect(find.text('article title'), findsNothing);
    expect(find.text('recipe title'), findsOneWidget);
    final tutorial = find.widgetWithText(FilterChip, 'Gym');
    expect(tutorial, findsOneWidget);
    await tester.ensureVisible(tutorial);
    await tester.pumpAndSettle();
    await tester.tap(tutorial);
    await tester.pumpAndSettle();
    expect(asked.last, 'Gym');
    expect(find.text('tutorial title'), findsOneWidget);
    expect(find.text('recipe title'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.runAsync(services.dispose);
  });
}
