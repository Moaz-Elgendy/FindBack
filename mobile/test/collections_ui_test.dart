import 'package:findback/app_services.dart';
import 'package:findback/features/account/account_page.dart';
import 'package:findback/services/auth_service.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/features/collections/collections_page.dart';
import 'package:findback/features/collections/library_shell.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/memory_collection.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  setUpAll(() {
    sqfliteFfiInit(); databaseFactory = databaseFactoryFfi;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });
  for (final brightness in Brightness.values) {
    testWidgets('collection covers support narrow ${brightness.name} and large text', (tester) async {
      tester.view.physicalSize = const Size(320, 740); tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize); addTearDown(tester.view.resetDevicePixelRatio);
      final c = MemoryCollection(id: 'a', name: 'Claude tools for planning and building', urls: ['https://example.com']);
      final item = ItemDetail.fromJson({'id': 'a', 'url': c.urls.first, 'title_clean': 'Planning a developer workflow with Claude Code'});
      var tapped = false;
      await tester.pumpWidget(MaterialApp(theme: ThemeData(brightness: brightness, colorSchemeSeed: const Color(0xFF1769AA)),
        builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!),
        home: Scaffold(body: SizedBox(height: 350, child: CollectionCover(collection: c, memories: [item], onTap: () => tapped = true)))));
      expect(find.text('1 memory'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text(c.name)); expect(tapped, isTrue);
    });
  }
  testWidgets('Collections refreshes on resume, creates locally, and Account resets confirmations', (tester) async {
    final db = (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
    final api = ApiClient();
    final services = AppServices(db: db, api: api, guest: true, items: GuestLibrary(db, api).items,
      sync: SyncService(pending: () async => [], send: (_) async => const SyncBatchResult(mapped: [], failedClientIds: []),
        apply: (_) async {}, markFailed: (_) async {}, isOnline: () async => false),
      capture: CaptureService(ingest: (_, __, ___) async => throw UnimplementedError(), queue: (_, __, ___) async => 'local'),
      share: ShareIntentService());
    Future<void> drain() async {
      for (var i = 0; i < 10; i++) {
        await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 50)));
        await tester.pump(const Duration(milliseconds: 100));
      }
      expect(find.byType(LinearProgressIndicator), findsNothing);
      await tester.pumpAndSettle();
    }
    await tester.pumpWidget(MaterialApp(home: LibraryShell(services: services)));
    await drain();
    await tester.tap(find.byIcon(Icons.collections_bookmark_outlined)); await drain();
    expect(find.text('Your collections start here'), findsOneWidget);
    expect(find.textContaining('Kept on this device'), findsOneWidget);
    expect(find.byType(RefreshIndicator), findsNothing);
    await tester.runAsync(() => services.collections.save(name: 'Resume collection', urls: []));
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await drain();
    expect(find.text('Resume collection'), findsNWidgets(2));
    await tester.tap(find.text('New collection')); await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'My tools');
    await tester.tap(find.text('Continue')); await tester.pumpAndSettle();
    expect(find.text('Choose memories'), findsOneWidget);
    await tester.tap(find.text('Save')); await drain();
    expect(find.text('My tools'), findsWidgets);
    await tester.tap(find.byIcon(Icons.bookmarks_outlined)); await tester.pumpAndSettle();
    expect(find.text('FindBack'), findsOneWidget);
    await tester.runAsync(() => services.db.setDeleteConfirmationSuppressed(true));
    FlutterSecureStorage.setMockInitialValues({});
    final auth = AuthService();
    await tester.pumpWidget(AppServicesScope(services: services,
      child: MaterialApp(home: AccountPage(auth: auth))));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Show delete confirmations'));
    await tester.tap(find.text('Show delete confirmations'));
    await drain();
    expect(await tester.runAsync(() => services.db.deleteConfirmationSuppressed), isFalse);
    await tester.pumpWidget(const SizedBox());
    await tester.runAsync(auth.dispose);
    await tester.pumpWidget(const SizedBox());
    var disposed = false;
    final closing = services.dispose().then((_) => disposed = true);
    for (var i = 0; i < 100 && !disposed; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 5)));
      await tester.pump();
    }
    expect(disposed, isTrue);
    await closing;
  });
}
