import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/features/account/account_page.dart';
import 'package:findback/models/weekly_note_settings.dart';
import 'package:findback/services/appearance.dart';
import 'package:findback/services/auth_service.dart';
import 'package:findback/theme.dart';
import 'reminder_service_test.dart' show FakeNotifications;

class AccountApi extends ApiClient {
  WeeklyNoteSettings settings = const WeeklyNoteSettings();
  bool offline = false;
  int writes = 0, exports = 0;
  Completer<Map<String, dynamic>>? exportGate;
  @override Future<WeeklyNoteSettings> weeklyNoteSettings() async {
    if (offline) throw ApiException('Could not reach your account.');
    return settings;
  }
  @override Future<WeeklyNoteSettings> saveWeeklyNoteSettings(WeeklyNoteSettings value) async {
    if (offline) throw ApiException('Could not save your settings.');
    writes++;
    return settings = value;
  }
  @override Future<Map<String, dynamic>> exportSaves() async {
    exports++;
    if (exportGate != null) return exportGate!.future;
    return {'version': 1, 'saves': [{'id': 'own-save', 'title': 'My edit', 'summary': 'My brief'}]};
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    for (final (family, asset) in [
      ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf'),
      ('Schibsted Grotesk', 'assets/fonts/SchibstedGrotesk.ttf'),
      ('Source Serif 4', 'assets/fonts/SourceSerif4.ttf'),
      ('Noto Sans Arabic', 'assets/fonts/noto-sans-arabic/NotoSansArabic.ttf'),
    ]) {
      await (FontLoader(family)..addFont(rootBundle.load(asset))).load();
    }
  });
  late AuthService auth;
  late AccountApi api;
  late FakeNotifications notifications;
  late Appearance appearance;
  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    auth = AuthService();
    auth.session.value = AuthSession(id: 'user-a', email: 'person@example.test',
      accessToken: 'access', refreshToken: 'refresh', expiresAt: DateTime.now().add(const Duration(hours: 1)));
    api = AccountApi();
    notifications = FakeNotifications();
    appearance = Appearance();
  });
  tearDown(() async { await auth.dispose(); api.close(); appearance.dispose(); });

  Widget app({Brightness brightness = Brightness.light, double scale = 1,
    TextDirection direction = TextDirection.ltr, Future<void> Function()? onDelete,
    Future<void> Function(Map<String, dynamic>)? onExport, GlobalKey? capture}) => AppearanceScope(
      appearance: appearance, child: MaterialApp(theme: FindBackTheme.build(brightness),
        debugShowCheckedModeBanner: false,
        builder: (context, child) => RepaintBoundary(key: capture, child: MediaQuery(data: MediaQuery.of(context).copyWith(
          textScaler: TextScaler.linear(scale)), child: Directionality(textDirection: direction, child: child!))),
        home: AccountPage(auth: auth, api: api, notifications: notifications,
          onDelete: onDelete, onExport: onExport)));

  testWidgets('weekly opt-in explains before requesting; denied preference survives and Settings opens', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(notifications.requests, 0);
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.textContaining("We'll send one notification a week"), findsOneWidget);
    expect(api.writes, 0);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(api.settings.enabled, isTrue);
    expect(notifications.requests, 1);
    expect(find.textContaining('Sunday at'), findsOneWidget);
    await tester.tap(find.text('Open settings'));
    await tester.pumpAndSettle();
    expect(notifications.settings, 1);
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(api.settings.enabled, isFalse);
    expect(notifications.requests, 1);
  });

  testWidgets('cancel permission context leaves weekly preference off', (tester) async {
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Not now'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    expect(notifications.requests, 0);
  });

  testWidgets('load failure retries and save failure keeps previous settings', (tester) async {
    api.offline = true;
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(find.text('Could not load your weekly note settings.'), findsOneWidget);
    api.offline = false;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    api.offline = true;
    notifications.allowed = true;
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(api.settings.enabled, isFalse);
    expect(find.text('Could not save your settings.'), findsOneWidget);
    expect(notifications.requests, 0);
  });

  testWidgets('installing the new Account API reloads settings after a session change', (tester) async {
    var currentApi = api;
    late StateSetter rebuild;
    await tester.pumpWidget(MaterialApp(home: StatefulBuilder(builder: (context, setState) {
      rebuild = setState;
      return AccountPage(auth: auth, api: currentApi, notifications: notifications);
    })));
    await tester.pumpAndSettle();
    api.offline = true;
    auth.session.value = AuthSession(id: 'user-b', email: 'b@example.test', accessToken: 'b',
      refreshToken: 'b', expiresAt: DateTime.now().add(const Duration(hours: 1)));
    await tester.pumpAndSettle();
    expect(find.text('Could not load your weekly note settings.'), findsOneWidget);
    final next = AccountApi()..settings = const WeeklyNoteSettings(enabled: true, weekday: 0);
    rebuild(() => currentApi = next);
    await tester.pumpAndSettle();
    expect(find.text('Could not load your weekly note settings.'), findsNothing);
    expect(find.textContaining('Monday at'), findsOneWidget);
    expect(tester.widget<Switch>(find.byType(Switch)).value, isTrue);
    await tester.pumpWidget(const SizedBox());
    next.close();
  });

  testWidgets('export shares actual account payload; deletion needs confirmation and failures remain visible', (tester) async {
    Map<String, dynamic>? exported;
    var deletes = 0;
    await tester.pumpWidget(app(onExport: (data) async => exported = data,
      onDelete: () async { deletes++; throw ApiException('Account deletion unavailable.'); }));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Export my saves'));
    await tester.tap(find.text('Export my saves'));
    await tester.pumpAndSettle();
    expect(api.exports, 1);
    expect((exported!['saves'] as List).single['title'], 'My edit');
    await tester.ensureVisible(find.text('Delete account'));
    await tester.tap(find.text('Delete account'));
    await tester.pumpAndSettle();
    expect(deletes, 0);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(deletes, 0);
    await tester.tap(find.text('Delete account'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Delete account'));
    await tester.pumpAndSettle();
    expect(deletes, 1);
    await tester.ensureVisible(find.text('Account deletion unavailable.'));
    expect(find.text('Account deletion unavailable.'), findsOneWidget);
    expect(auth.currentSession, isNotNull);
  });

  testWidgets('account switch during export aborts sharing the previous account data', (tester) async {
    api.exportGate = Completer<Map<String, dynamic>>();
    var shares = 0;
    await tester.pumpWidget(app(onExport: (_) async { shares++; }));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Export my saves'));
    await tester.tap(find.text('Export my saves'));
    await tester.pumpAndSettle();
    expect(api.exports, 1);
    auth.session.value = AuthSession(id: 'user-b', email: 'b@example.test', accessToken: 'b',
      refreshToken: 'b', expiresAt: DateTime.now().add(const Duration(hours: 1)));
    api.exportGate!.complete({'version': 1, 'saves': [{'title': 'A private title'}]});
    await tester.pumpAndSettle();
    expect(shares, 0);
    expect(find.text('Your account changed. Please try again.'), findsOneWidget);
  });

  testWidgets('guest keeps sign-in flow and cannot enable a weekly note or delete an account', (tester) async {
    auth.session.value = null;
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(find.text('Sign in'), findsOneWidget);
    expect(find.text('Delete account'), findsNothing);
    final toggle = tester.widget<Switch>(find.byType(Switch));
    expect(toggle.onChanged, isNull);
    expect(notifications.requests, 0);
  });

  testWidgets('weekly schedule changes only after choosing a day and time', (tester) async {
    api.settings = const WeeklyNoteSettings(enabled: true);
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.textContaining('Sunday at'));
    await tester.tap(find.textContaining('Sunday at'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Monday'));
    await tester.pumpAndSettle();
    expect(api.writes, 0);
    await tester.tap(find.text('OK'));
    await tester.pumpAndSettle();
    expect(api.settings.weekday, 0);
    expect(api.settings.timeZone, 'UTC');
    expect(api.writes, 1);
  });

  for (final brightness in Brightness.values) {
    for (final width in [320.0, 360.0, 390.0]) {
      testWidgets('Guest Account ${brightness.name} ${width}dp at text 2', (tester) async {
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = Size(width, 800);
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        auth.session.value = null;
        await tester.pumpWidget(app(brightness: brightness, scale: 2, direction: TextDirection.rtl));
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text('Sign in'));
        await tester.tap(find.text('Sign in'));
        await tester.pumpAndSettle();
        expect(find.text('Enter a valid email address.'), findsOneWidget);
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.text('Appearance'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
      });
      for (final scale in [1.0, 1.3, 2.0]) {
        testWidgets('Account ${brightness.name} ${width}dp text $scale', (tester) async {
          tester.view.devicePixelRatio = 1;
          tester.view.physicalSize = Size(width, 800);
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          api.settings = const WeeklyNoteSettings(enabled: true);
          final capture = GlobalKey();
          await tester.pumpWidget(app(brightness: brightness, scale: scale,
            direction: scale == 2 ? TextDirection.rtl : TextDirection.ltr, onDelete: () async {}, capture: capture));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          if (const bool.fromEnvironment('CAPTURE_REDESIGN') && scale == 1 && width != 320) {
            await tester.runAsync(() async {
              final image = await (capture.currentContext!.findRenderObject()! as RenderRepaintBoundary).toImage();
              final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
              await File('/tmp/findback-account-${brightness.name}-${width.toInt()}.png').writeAsBytes(bytes!.buffer.asUint8List());
              image.dispose();
            });
          }
          for (final label in ['Appearance', 'Your data', 'Delete account']) {
            await tester.ensureVisible(find.text(label));
            await tester.pumpAndSettle();
            expect(tester.takeException(), isNull);
          }
          await tester.ensureVisible(find.text('Dark'));
          await tester.tap(find.text('Dark'));
          await tester.pumpAndSettle();
          expect(appearance.mode, ThemeMode.dark);
          expect(tester.takeException(), isNull);
        });
      }
    }
  }
}
