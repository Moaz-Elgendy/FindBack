import 'package:findback/app.dart';
import 'package:findback/app_services.dart';
import 'package:findback/data/api_client.dart';
import 'package:findback/data/local_db.dart';
import 'package:findback/features/account/account_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/appearance.dart';
import 'package:findback/services/auth_service.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/services/items_service.dart';
import 'package:findback/services/share_intent_service.dart';
import 'package:findback/services/sync_service.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
  });
  setUp(() => FlutterSecureStorage.setMockInitialValues({}));

  test('new installs use Auto without a stored preference', () async {
    final appearance = Appearance();
    await appearance.restore();
    expect(appearance.mode, ThemeMode.system);
  });

  test('Light, Dark and Auto survive a new settings instance', () async {
    for (final mode in [ThemeMode.dark, ThemeMode.system, ThemeMode.light]) {
      await Appearance().select(mode);
      final restored = Appearance();
      await restored.restore();
      expect(restored.mode, mode);
    }
  });

  test('an invalid stored theme falls back to Auto', () async {
    FlutterSecureStorage.setMockInitialValues(
        {'findback.appearance': 'invalid'});
    final appearance = Appearance();
    await appearance.restore();
    expect(appearance.mode, ThemeMode.system);
  });

  test('a storage failure does not change the active theme', () async {
    final appearance = Appearance(storage: _FailingStorage());
    await expectLater(
        appearance.select(ThemeMode.dark), throwsA(isA<PlatformException>()));
    expect(appearance.mode, ThemeMode.system);
  });

  testWidgets('Account reports an appearance storage failure', (tester) async {
    final appearance = Appearance(storage: _FailingStorage());
    await tester.pumpWidget(AppearanceScope(
      appearance: appearance,
      child: MaterialApp(home: AccountPage(auth: AuthService())),
    ));
    await tester.ensureVisible(find.text('Dark'));
    await tester.tap(find.text('Dark'));
    await tester.pumpAndSettle();
    expect(find.text('Could not save appearance. Try again.'), findsOneWidget);
    expect(appearance.mode, ThemeMode.system);
    await tester.pumpWidget(const SizedBox());
    appearance.dispose();
  });

  test('control borders and Recipe colors meet contrast in both themes', () {
    double contrast(Color a, Color b) {
      final first = a.computeLuminance(), second = b.computeLuminance();
      return first > second
          ? (first + .05) / (second + .05)
          : (second + .05) / (first + .05);
    }

    for (final brightness in Brightness.values) {
      final palette = brightness == Brightness.dark
          ? FindBackTheme.dark
          : FindBackTheme.light;
      final theme = FindBackTheme.build(brightness);
      expect(
          contrast(
              palette[FindBackColor.controlLine]!, theme.colorScheme.surface),
          greaterThanOrEqualTo(3));
      expect(
          contrast(palette[FindBackColor.recipeInk]!,
              palette[FindBackColor.recipeBackground]!),
          greaterThanOrEqualTo(4.5));
      expect(theme.chipTheme.side!.color, palette[FindBackColor.controlLine]);
      expect(theme.textTheme.bodyMedium!.fontFamilyFallback,
          contains('Noto Sans Arabic'));
      expect(FindBackTheme.summaryStyle.fontFamilyFallback,
          contains('Noto Sans Arabic'));
    }
  });

  test('both themes use the brief colors, typography and shapes', () {
    for (final brightness in Brightness.values) {
      final theme = FindBackTheme.build(brightness);
      final dark = brightness == Brightness.dark;
      expect(
          theme.scaffoldBackgroundColor, Color(dark ? 0xFF0A1715 : 0xFFEDF7F5));
      expect(theme.colorScheme.primary, Color(dark ? 0xFF35BFB1 : 0xFF0F766E));
      expect(
          theme.colorScheme.onPrimary, Color(dark ? 0xFF04201D : 0xFFFFFFFF));
      expect(theme.colorScheme.error, Color(dark ? 0xFFFF8A7D : 0xFFB42318));
      expect(theme.textTheme.bodyMedium!.fontFamily, 'Schibsted Grotesk');
      expect(theme.textTheme.bodyMedium!.fontSize, 15);
      expect(theme.textTheme.bodySmall!.fontSize, 12.5);
      expect(FindBackTheme.summaryStyle.fontFamily, 'Source Serif 4');
      expect((theme.cardTheme.shape! as RoundedRectangleBorder).borderRadius,
          BorderRadius.circular(22));
      final muted = theme.colorScheme.onSurfaceVariant;
      for (final background in [
        theme.scaffoldBackgroundColor,
        theme.cardTheme.color!
      ]) {
        final a = muted.computeLuminance(), b = background.computeLuminance();
        expect((a > b ? (a + .05) / (b + .05) : (b + .05) / (a + .05)),
            greaterThanOrEqualTo(4.5));
      }
    }
  });

  for (final width in [360.0, 390.0]) {
    testWidgets(
        'Account appearance switches the app and Auto follows OS at $width',
        (tester) async {
      tester.view.physicalSize = Size(width, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.dark;
      addTearDown(tester.platformDispatcher.clearPlatformBrightnessTestValue);
      final db =
          (await tester.runAsync(() => LocalDb.openAt(inMemoryDatabasePath)))!;
      final api = ApiClient();
      final services = AppServices(
        db: db,
        api: api,
        capture: CaptureService.of(db: db, api: api),
        items: ItemsService(
          remoteItem: (_) async => throw UnimplementedError(),
          localItem: (_) async => null,
          remoteRecent: (_, {category, cursor, filters}) async =>
              ItemPage(items: []),
          localRecent: (_, {category, filters}) async => [],
          cache: (_) async {},
          remoteDelete: (_) async {},
          localDelete: (_) async => 0,
          dropQueued: (_) async => 0,
          isOnline: () async => true,
        ),
        share: ShareIntentService(),
        sync: SyncService(
            pending: () async => [],
            send: (_) async => throw UnimplementedError(),
            apply: (_) async {},
            markFailed: (_) async => {},
            isOnline: () async => false),
      );
      final appearance = Appearance();
      await tester
          .pumpWidget(FindBackApp(services: services, appearance: appearance));
      await tester.pumpAndSettle();
      final navigator =
          tester.state<NavigatorState>(find.byType(Navigator).first);
      navigator.push(MaterialPageRoute<void>(
          builder: (_) => AccountPage(auth: AuthService())));
      await tester.pumpAndSettle();
      Brightness active() =>
          Theme.of(tester.element(find.text('Appearance'))).brightness;
      expect(active(), Brightness.dark);
      await tester.ensureVisible(find.text('Light'));
      await tester.tap(find.text('Light'));
      await tester.pumpAndSettle();
      expect(active(), Brightness.light);
      await tester.tap(find.text('Dark'));
      await tester.pumpAndSettle();
      expect(active(), Brightness.dark);
      await tester.tap(find.text('Auto'));
      await tester.pumpAndSettle();
      expect(active(), Brightness.dark);
      tester.platformDispatcher.platformBrightnessTestValue = Brightness.light;
      await tester.pumpAndSettle();
      expect(active(), Brightness.light);
      final restored = Appearance();
      await restored.restore();
      expect(restored.mode, ThemeMode.system);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      await tester.runAsync(services.dispose);
      appearance.dispose();
    });
  }
}

class _FailingStorage extends FlutterSecureStorage {
  @override
  Future<void> write(
      {required String key,
      required String? value,
      AppleOptions? iOptions,
      AndroidOptions? aOptions,
      LinuxOptions? lOptions,
      WebOptions? webOptions,
      AppleOptions? mOptions,
      WindowsOptions? wOptions}) async {
    throw PlatformException(code: 'storage_unavailable');
  }
}
