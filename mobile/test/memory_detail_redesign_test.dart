import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:timezone/data/latest.dart' as zones;
import 'package:findback/app_services.dart';
import 'package:findback/app.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/features/home/detail_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/guest_library.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/reminder_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:findback/theme.dart';
import 'reminder_service_test.dart' show FakeNotifications;

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    sqfliteFfiInit(); databaseFactory = databaseFactoryFfi; zones.initializeTimeZones();
    for (final (family, asset) in [('MaterialIcons','fonts/MaterialIcons-Regular.otf'),
      ('Schibsted Grotesk','assets/fonts/SchibstedGrotesk.ttf'),
      ('Source Serif 4','assets/fonts/SourceSerif4.ttf'),
      ('Noto Sans Arabic','assets/fonts/noto-sans-arabic/NotoSansArabic.ttf')]) {
      await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
    }
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel(ShareIntentService.channelName), (_) async => null);
  });
  final item = ItemDetail.fromJson({'id':'memory','url':'https://youtube.com/watch?v=example',
    'title':'Five practical ways to remember what you save', 'status':'ready', 'content_type':'video',
    'source_domain':'youtube.com','created_at':'2026-10-01T12:00:00Z','summary':'Keep useful ideas close. Find them by describing one thing you remember.',
    'key_points_with_refs':[{'point':'Save the ideas you want to return to later.','source_ref':'0:42'},
      {'point':'Describe what you remember when you need it.','source_ref':'1:35'}]});
  Future<AppServices> setup(WidgetTester tester, {bool allowed=true, ItemDetail? record}) async {
    zones.initializeTimeZones();
    final db = (await tester.runAsync(()=>LocalDb.openAt(inMemoryDatabasePath)))!;
    await tester.runAsync(()=>db.upsertRemoteItems([record ?? item]));
    final api = ApiClient();
    return AppServices(db:db,api:api,guest:true,guestLibrary:GuestLibrary(db,api),
      items:ItemsService.of(db:db,api:api,isOnline:()async=>false),
      sync:SyncService(pending:db.pendingQueue,send:api.syncBatch,apply:db.applyMapped,markFailed:db.markQueueFailed,isOnline:()async=>false),
      capture:CaptureService.of(db:db,api:api),share:ShareIntentService(),
      reminders:ReminderService(db:db,api:api,notifications:FakeNotifications()..allowed=allowed,
        guest:true,clock:()=>DateTime.utc(2026,10,8,12)));
  }
  Future<void> settle(WidgetTester tester) async {
    for (var i=0;i<20;i++) {
      await tester.runAsync(()=>Future<void>.delayed(const Duration(milliseconds:10)));
      await tester.pump(const Duration(milliseconds:50));
    }
    await tester.pumpAndSettle();
  }
  Future<void> close(WidgetTester tester,AppServices services) async {
    await tester.pumpWidget(const SizedBox()); await tester.runAsync(services.dispose);
  }
  testWidgets('failed brief offers original sharing without a false ready copy', (tester) async {
    final failed = ItemDetail.fromJson({'id': 'failed', 'url': 'https://example.test/failed',
      'title': 'Failed source', 'status': 'ready', 'needs_retry': true, 'key_points': []});
    final services = await setup(tester, record: failed);
    await tester.pumpWidget(AppServicesScope(services: services, child: MaterialApp(
      theme: FindBackTheme.build(Brightness.light), home: DetailPage(itemId: failed.id,
        items: services.items, services: services))));
    await settle(tester);
    await tester.tap(find.text('Share'));
    await settle(tester);
    final primary = tester.widget<ListTile>(find.widgetWithText(ListTile, 'Share this memory'));
    expect(primary.enabled, isFalse);
    expect(primary.onTap, isNull);
    expect(find.text('Share original link'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.runAsync(services.dispose);
  });

  testWidgets('detail sets denied reminder, offers Settings and removes stored choice', (tester) async {
    final services=await setup(tester,allowed:false);
    await tester.pumpWidget(MaterialApp(theme:FindBackTheme.build(Brightness.light),home:DetailPage(itemId:item.id,items:services.items,services:services)));
    await settle(tester);
    expect(find.text('Jump to 0:42'),findsOneWidget);
    await tester.scrollUntilVisible(find.text('Remind me'), 200, scrollable: find.byType(Scrollable).first); await settle(tester); await tester.tap(find.text('Remind me')); await settle(tester);
    await tester.tap(find.text('Cancel')); await settle(tester);
    expect(await tester.runAsync(() => services.reminders!.current(item.id)), isNull);
    await tester.tap(find.text('Remind me')); await settle(tester); expect(find.text('Pick date and time'), findsOneWidget); await tester.tap(find.textContaining('Tomorrow,').first); await settle(tester);
    expect(find.text("We'll send one notification at the time you choose."),findsOneWidget);
    await tester.tap(find.text('Continue')); await settle(tester);
    expect(find.text('Notifications are off. Turn them on in Settings to get this reminder'),findsOneWidget); expect(await tester.runAsync(()=>services.reminders!.current(item.id)),isNotNull);
    await tester.tap(find.text('Open settings')); await settle(tester);
    expect((services.reminders!.notifications as FakeNotifications).settings,1);
    await tester.ensureVisible(find.textContaining('Reminder ·')); await tester.tap(find.textContaining('Reminder ·')); await settle(tester);
    await tester.tap(find.text('Remove reminder')); await settle(tester); expect(await tester.runAsync(()=>services.reminders!.current(item.id)),isNull); await close(tester,services);
  });
  testWidgets('edited detail asks before regeneration and Edit persists locally', (tester) async {
    final services=await setup(tester,record:ItemDetail.fromJson({'id':item.id,'url':item.url,'title':item.bestTitle,'summary':item.briefText,'status':'ready','edited':true}));
    await tester.pumpWidget(MaterialApp(home:DetailPage(itemId:item.id,items:services.items,services:services)));
    await settle(tester); await tester.tap(find.byTooltip('Memory actions')); await settle(tester);
    expect(find.text('Edit'),findsOneWidget); expect(find.text('Summarize again'),findsOneWidget); expect(find.text('Delete'),findsOneWidget);
    await tester.tap(find.text('Summarize again')); await settle(tester);
    expect(find.text('Replace your edits with a new summary?'),findsOneWidget);
    await tester.tap(find.text('Cancel')); await settle(tester);
    await tester.tap(find.byTooltip('Memory actions')); await settle(tester); await tester.tap(find.text('Edit')); await settle(tester);
    await tester.enterText(find.byType(TextFormField).first,'My edited memory');
    await tester.tap(find.text('Save')); await settle(tester);
    expect(find.text('My edited memory'),findsOneWidget); await close(tester,services);
  });
  testWidgets('notification tap opens its memory detail', (tester) async {
    final services = await setup(tester);
    await tester.pumpWidget(FindBackApp(services: services));
    await settle(tester);
    final notifications = services.reminders!.notifications as FakeNotifications;
    expect(notifications.requests, 0);
    notifications.onTap!(item.id);
    await settle(tester);
    expect(find.byType(DetailPage), findsOneWidget);
    expect(find.text('Jump to 0:42'), findsOneWidget);
    await close(tester, services);
  });
  testWidgets('detail copy lives in menu and copies title points and source', (tester) async {
    final services = await setup(tester);
    String? copied;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
      if (call.method == 'Clipboard.setData') copied = (call.arguments as Map)['text'] as String;
      return null;
    });
    addTearDown(() => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(SystemChannels.platform, null));
    await tester.pumpWidget(MaterialApp(theme: FindBackTheme.build(Brightness.light),
      home: DetailPage(itemId: item.id, items: services.items, services: services)));
    await settle(tester);
    expect(find.text('Copy summary'), findsNothing);
    await tester.tap(find.byTooltip('Memory actions'));
    await tester.pumpAndSettle();
    expect(find.text('Copy summary'), findsOneWidget);
    await tester.tap(find.text('Copy summary'));
    await tester.pumpAndSettle();
    expect(copied, startsWith(item.bestTitle));
    expect(copied, contains('1. '));
    expect(copied, contains('(0:42)'));
    expect(copied, endsWith(item.url));
    expect(find.text('Summary copied'), findsOneWidget);
    await close(tester, services);
  });

  testWidgets('detail confirms deletion then Undo restores memory and alarm', (tester) async {
    final services = await setup(tester);
    await tester.pumpWidget(MaterialApp(home: Builder(builder: (context) => Scaffold(
      body: TextButton(onPressed: () => Navigator.push(context, MaterialPageRoute<void>(builder: (_) =>
        DetailPage(itemId:item.id,items:services.items,services:services))), child: const Text('Open memory'))))));
    await settle(tester);
    final set = services.reminders!.set(item, DateTime.utc(2026,10,9,9), 'UTC', explainPermission: () async => true);
    await settle(tester); await set;
    await tester.tap(find.text('Open memory')); await settle(tester);
    await tester.tap(find.byTooltip('Memory actions')); await settle(tester);
    await tester.tap(find.text('Delete')); await settle(tester);
    expect(find.text('Delete memory?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Delete'));
    await settle(tester);
    expect(find.text('Delete memory?'), findsNothing);
    expect(find.text('Memory deleted'),findsOneWidget);
    expect(await tester.runAsync(()=>services.db.localItem(item.id)),isNull);
    expect((services.reminders!.notifications as FakeNotifications).scheduled,isEmpty);
    await tester.tap(find.text('Undo')); await settle(tester);
    expect(await tester.runAsync(()=>services.db.localItem(item.id)),isNotNull);
    expect((services.reminders!.notifications as FakeNotifications).scheduled.length,1);
    await close(tester,services);
  });
  testWidgets('active reminder label follows the current device zone after travel', (tester) async {
    final services=await setup(tester);
    await tester.pumpWidget(MaterialApp(home:DetailPage(itemId:item.id,items:services.items,services:services)));
    await settle(tester);
    final set=services.reminders!.set(item,DateTime.utc(2026,10,9,9),'UTC',explainPermission:() async=>true);
    await settle(tester); await set;
    (services.reminders!.notifications as FakeNotifications).zoneName='Asia/Tokyo';
    final update=services.reminders!.reconcile(); await settle(tester); await update;
    await tester.scrollUntilVisible(find.text('Reminder · Tomorrow, 6:00 PM'),200,scrollable:find.byType(Scrollable).first);
    await settle(tester);
    expect(find.text('Reminder · Tomorrow, 6:00 PM'),findsOneWidget);
    expect((await tester.runAsync(()=>services.reminders!.current(item.id)))!.scheduledAt,DateTime.utc(2026,10,9,9));
    await close(tester,services);
  });
  testWidgets('mixed Arabic detail and reminder sheet support RTL at 200%', (tester) async {
    tester.view.devicePixelRatio=1; tester.view.physicalSize=const Size(320,800);
    addTearDown(tester.view.resetDevicePixelRatio); addTearDown(tester.view.resetPhysicalSize);
    final arabic=ItemDetail.fromJson({'id':item.id,'url':item.url,'title':'تذكّر الأفكار Useful ideas',
      'summary':'احفظ فكرة مفيدة ثم ابحث عنها عندما تحتاج إليها. Save and find.', 'status':'ready'});
    final services=await setup(tester,record:arabic);
    await tester.pumpWidget(MaterialApp(theme:FindBackTheme.build(Brightness.dark),
      builder:(context,child)=>Directionality(textDirection:TextDirection.rtl,
        child:MediaQuery(data:MediaQuery.of(context).copyWith(textScaler:TextScaler.linear(2)),child:child!)),
      home:DetailPage(itemId:item.id,items:services.items,services:services)));
    await settle(tester); expect(tester.takeException(),isNull);
    await tester.scrollUntilVisible(find.text('Remind me'),200,scrollable:find.byType(Scrollable).first);
    await settle(tester); await tester.tap(find.text('Remind me')); await settle(tester);
    expect(find.text('Pick date and time'),findsOneWidget); expect(tester.takeException(),isNull);
    await close(tester,services);
  });
  for (final brightness in Brightness.values) {
    for (final width in [320.0,360.0,390.0]) {
      for (final scale in [1.0,1.3,2.0]) {
        testWidgets('detail ${brightness.name} ${width}dp text $scale', (tester) async {
          tester.view.devicePixelRatio=1; tester.view.physicalSize=Size(width,800);
          addTearDown(tester.view.resetDevicePixelRatio); addTearDown(tester.view.resetPhysicalSize);
          final services=await setup(tester);
          final boundary=GlobalKey();
          await tester.pumpWidget(MaterialApp(debugShowCheckedModeBanner:false,theme:FindBackTheme.build(brightness),
            builder:(context,child)=>MediaQuery(data:MediaQuery.of(context).copyWith(textScaler:TextScaler.linear(scale)),child:child!),
            home:RepaintBoundary(key:boundary,child:DetailPage(itemId:item.id,items:services.items,services:services))));
          await settle(tester); expect(tester.takeException(),isNull);
          expect(find.text('Open original'),findsOneWidget); expect(find.text('Share'),findsOneWidget);
          if (const bool.fromEnvironment('CAPTURE_REDESIGN') && scale==1 && width!=320) {
            await tester.runAsync(() async {
              final image=await (boundary.currentContext!.findRenderObject()! as RenderRepaintBoundary).toImage();
              final bytes=await image.toByteData(format:ui.ImageByteFormat.png);
              await File('/tmp/findback-detail-${brightness.name}-${width.toInt()}.png').writeAsBytes(bytes!.buffer.asUint8List()); image.dispose();
            });
          }
          await tester.scrollUntilVisible(find.text('Remind me'), 200, scrollable: find.byType(Scrollable).first); await settle(tester); await tester.tap(find.text('Remind me')); await settle(tester); expect(find.text('Pick date and time'), findsOneWidget);
          expect(tester.takeException(),isNull); await close(tester,services);
        });
      }
    }
  }
}
