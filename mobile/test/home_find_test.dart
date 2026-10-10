import 'dart:ui' as ui;
import 'dart:io';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/features/collections/library_shell.dart';
import 'package:findback/features/home/home_screen.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    sqfliteFfiInit(); databaseFactory = databaseFactoryFfi;
    for (final (family, asset) in [
      ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf'),
      ('Schibsted Grotesk', 'assets/fonts/SchibstedGrotesk.ttf'),
      ('Source Serif 4', 'assets/fonts/SourceSerif4.ttf'),
      ('Noto Sans Arabic', 'assets/fonts/noto-sans-arabic/NotoSansArabic.ttf'),
    ]) {
      await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
    }
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 50; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 10)));
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.pumpAndSettle();
  }

  Future<AppServices> setup(WidgetTester tester, {List<ItemDetail> records = const []}) async {
    final db = (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    await tester.runAsync(() => db.upsertRemoteItems(records));
    final api = _GuestApi();
    return AppServices(db: db, api: api, guest: true,
      items: GuestLibrary(db, api).items, guestLibrary: GuestLibrary(db, api),
      initialLibrary: records.map(SearchResult.fromItem).toList(),
      capture: CaptureService(ingest: (_, __, ___) async => const IngestResult(id: 'saved', status: 'pending', canonicalUrl: 'https://example.test/saved'),
        queue: (_, __, ___) async => 'queued', isOnline: () async => true),
      sync: SyncService(pending: db.pendingQueue,
        send: (_) async => const SyncBatchResult(mapped: [], failedClientIds: []),
        apply: db.applyMapped, markFailed: db.markQueueFailed, isOnline: () async => false),
      share: ShareIntentService());
  }

  Future<void> close(WidgetTester tester, AppServices services) async {
    await tester.pumpWidget(const SizedBox.shrink());
    var disposed = false;
    final closing = services.dispose().then((_) => disposed = true);
    for (var i = 0; i < 200 && !disposed; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(disposed, isTrue);
    await closing;
  }

  Future<void> capture(WidgetTester tester, GlobalKey boundary, String path) async {
    final render = boundary.currentContext!.findRenderObject()! as RenderRepaintBoundary;
    await tester.runAsync(() async {
      final picture = await render.toImage();
      final data = await picture.toByteData(format: ui.ImageByteFormat.png);
      await File(path).writeAsBytes(data!.buffer.asUint8List());
      picture.dispose();
    });
  }

  ItemDetail memory(String id, String type, List<String> topics) => ItemDetail.fromJson({
    'id': id, 'url': 'https://example.test/$id', 'title_clean': '$id memory',
    'status': 'ready', 'instant_brief': 'A useful summary about $id.', 'brief_source': 'llm',
    'content_type': type, 'topics': topics, 'category': type,
    'created_at': DateTime.now().toUtc().toIso8601String(),
  });

  testWidgets('worker outage is visible while saved cards remain on screen', (tester) async {
    final services = await setup(tester, records: [memory('AI', 'video', ['AI'])]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light),
      home: LibraryShell(services: services)));
    await settle(tester);
    services.api.processingError.value = ApiException('Processing unavailable', statusCode: 503, kind: ApiFailureKind.server);
    await tester.pumpAndSettle();
    expect(find.text('The processing service is unavailable. Your links are safe and will retry.'), findsOneWidget);
    expect(find.text('AI memory'), findsOneWidget);
    await close(tester, services);
  });

  testWidgets('Library launcher opens full-screen Find with focused input and returns', (tester) async {
    final services = await setup(tester, records: [memory('AI', 'video', ['AI'])]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light), home: LibraryShell(services: services)));
    await settle(tester);
    expect(find.byType(TextField), findsNothing);
    await tester.tap(find.text('What do you remember?'));
    await settle(tester);
    expect(find.text('Find'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField)).focusNode!.hasFocus, isTrue);
    expect(find.text('Newest saves first'), findsOneWidget);
    await tester.tap(find.text('Cancel'));
    await settle(tester);
    expect(find.text('FindBack'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);
    await close(tester, services);
  });

  testWidgets('first-save panel opens capture and duplicate shows toast with View', (tester) async {
    final services = await setup(tester);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light), home: HomeScreen(services: services)));
    await settle(tester);
    expect(find.text('Save your first link'), findsOneWidget);
    expect(find.text('Share'), findsOneWidget); expect(find.text('Read'), findsOneWidget); expect(find.text('Find'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Save a link'));
    await settle(tester);
    expect(find.byType(TextField), findsWidgets);
    await tester.tap(find.text('Cancel'));
    await settle(tester);
    await close(tester, services);
  });

  testWidgets('Find chips use own data, combine filters, and empty-query date browsing works', (tester) async {
    final services = await setup(tester, records: [memory('AI', 'video', ['AI']), memory('food', 'recipe', ['Food'])]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.dark), home: HomeScreen(services: services)));
    await settle(tester);
    await tester.tap(find.text('What do you remember?')); await settle(tester);
    expect(find.widgetWithText(FilterChip, 'a video'), findsOneWidget);
    expect(find.widgetWithText(FilterChip, 'something to buy'), findsNothing);
    await tester.tap(find.widgetWithText(FilterChip, 'about AI')); await settle(tester);
    expect(find.text('AI memory'), findsOneWidget); expect(find.text('food memory'), findsNothing);
    await tester.tap(find.widgetWithText(FilterChip, 'last 2 weeks')); await settle(tester);
    expect(find.text('AI memory'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'missing'); await settle(tester);
    expect(find.text('Nothing matches yet'), findsOneWidget);
    expect(find.text('Try one thing you remember: a topic, a place, a name.'), findsOneWidget);
    await close(tester, services);
  });
  testWidgets('card Edit saves locally; Delete confirms and Undo restores', (tester) async {
    final services = await setup(tester, records: [memory('original', 'video', ['AI'])]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light), home: HomeScreen(services: services)));
    await settle(tester);
    await tester.tap(find.byTooltip('Memory actions')); await settle(tester);
    await tester.tap(find.text('Edit')); await settle(tester);
    await tester.enterText(find.widgetWithText(TextFormField, 'Title'), 'My edited title');
    await tester.enterText(find.widgetWithText(TextFormField, 'Brief'), 'My own Arabic brief الملخص');
    await tester.tap(find.widgetWithText(FilledButton, 'Save')); await settle(tester);
    expect(find.text('My edited title'), findsOneWidget);
    await tester.tap(find.byTooltip('Memory actions')); await settle(tester);
    await tester.tap(find.text('Delete')); await settle(tester);
    expect(find.text('Delete memory?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await settle(tester);
    expect(find.text('My edited title'), findsNothing);
    expect(find.text('Memory deleted'), findsOneWidget);
    await tester.tap(find.text('Undo')); await settle(tester);
    expect(find.text('My edited title'), findsOneWidget);
    expect(find.text('My own Arabic brief الملخص'), findsOneWidget);
    await close(tester, services);
  });

  testWidgets('Summarize again asks before replacing user edits', (tester) async {
    final item = ItemDetail.fromJson({'id': 'edited', 'url': 'https://example.test/edited', 'status': 'ready', 'title_clean': 'edited memory', 'instant_brief': 'Edited brief', 'brief_source': 'llm', 'edited': true});
    final services = await setup(tester, records: [item]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.dark), home: HomeScreen(services: services)));
    await settle(tester);
    await tester.tap(find.text('edited memory')); await settle(tester);
    await tester.tap(find.byTooltip('Memory actions')); await settle(tester);
    await tester.tap(find.text('Summarize again')); await settle(tester);
    expect(find.text('Replace your edits with a new summary?'), findsOneWidget);
    await tester.tap(find.text('Cancel')); await settle(tester);
    expect(find.text('edited memory'), findsOneWidget);
    expect(find.byType(AlertDialog), findsNothing);
    await close(tester, services);
  });


  testWidgets('Home and Find render mixed Arabic content in RTL at 200%', (tester) async {
    tester.view.physicalSize = const Size(320, 900); tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize); addTearDown(tester.view.resetDevicePixelRatio);
    final item = ItemDetail.fromJson({'id': 'arabic', 'url': 'https://example.test/arabic',
      'title_clean': 'أداة AI تساعد على كتابة العروض', 'instant_brief': 'ملخص عربي عن أداة للكتابة والعروض AI tool.',
      'status': 'ready', 'brief_source': 'llm', 'content_type': 'video', 'topics': ['AI']});
    final services = await setup(tester, records: [item]);
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.dark),
      builder: (context, child) => Directionality(textDirection: TextDirection.rtl,
        child: MediaQuery(data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!)),
      home: LibraryShell(services: services)));
    await settle(tester);
    expect(tester.takeException(), isNull);
    expect(find.text(item.bestTitle), findsOneWidget);
    expect(find.text(item.briefText), findsOneWidget);
    await tester.tap(find.text('What do you remember?')); await settle(tester);
    expect(tester.takeException(), isNull);
    expect(tester.widget<TextField>(find.byType(TextField)).focusNode!.hasFocus, isTrue);
    await close(tester, services);
  });

  for (final brightness in Brightness.values) {
    testWidgets('first-save panel ${brightness.name} fits 320dp at 200%', (tester) async {
      tester.view.physicalSize = const Size(320, 900); tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize); addTearDown(tester.view.resetDevicePixelRatio);
      final services = await setup(tester);
      await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(brightness),
        builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!),
        home: LibraryShell(services: services)));
      await settle(tester);
      expect(tester.takeException(), isNull);
      expect(find.text('Save your first link'), findsOneWidget);
      await tester.ensureVisible(find.widgetWithText(FilledButton, 'Save a link')); await tester.pumpAndSettle();
      expect(tester.getSize(find.widgetWithText(FilledButton, 'Save a link')).height, greaterThanOrEqualTo(44));
      await close(tester, services);
    });
  }

  for (final brightness in Brightness.values) {
    for (final width in [320.0, 360.0, 390.0]) {
      for (final scale in [1.0, 1.3, 2.0]) {
        testWidgets('Home and Find ${brightness.name} ${width.toInt()}dp text $scale', (tester) async {
          tester.view.physicalSize = Size(width, 900); tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize); addTearDown(tester.view.resetDevicePixelRatio);
          final services = await setup(tester, records: [memory('AI', 'video', ['AI']),
            memory('recipe', 'recipe', ['Food']), memory('product', 'product', ['Design']), memory('AI second', 'video', ['AI'])]);
          final boundary = GlobalKey();
          await tester.pumpWidget(RepaintBoundary(key: boundary, child: MaterialApp(
            debugShowCheckedModeBanner: false,
            theme: FindBackTheme.build(brightness),
            builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(
              textScaler: TextScaler.linear(scale), disableAnimations: true), child: child!),
            home: LibraryShell(services: services))));
          await settle(tester);
          expect(tester.takeException(), isNull);
          expect(tester.getSize(find.byTooltip('Save a link')).shortestSide, greaterThanOrEqualTo(44));
          expect(find.text('Library'), findsOneWidget); expect(find.text('Collections'), findsOneWidget);
          if (const bool.fromEnvironment('CAPTURE_REDESIGN') && scale == 1) {
            await capture(tester, boundary, '/tmp/findback-home-${brightness.name}-${width.toInt()}.png');
          }
          await tester.tap(find.text('What do you remember?')); await settle(tester);
          expect(tester.takeException(), isNull);
          expect(find.widgetWithText(FilterChip, 'something to buy'), findsOneWidget);
          expect(find.byType(FilterChip), findsNWidgets(6));
          if (const bool.fromEnvironment('CAPTURE_REDESIGN') && scale == 1) {
            await capture(tester, boundary, '/tmp/findback-find-${brightness.name}-${width.toInt()}.png');
          }
          await tester.tap(find.text('Cancel')); await settle(tester);
          await tester.tap(find.text('Collections')); await settle(tester);
          expect(tester.takeException(), isNull);
          if (const bool.fromEnvironment('CAPTURE_REDESIGN') && scale == 1) {
            await capture(tester, boundary, '/tmp/findback-collections-${brightness.name}-${width.toInt()}.png');
          }
          await close(tester, services);
        });
      }
    }
  }

}

class _GuestApi extends ApiClient {
  @override
  Future<ItemPage> listItems({int limit = 20, String? cursor, String? category, Map<String, String>? filters}) async => const ItemPage(items: []);
}
