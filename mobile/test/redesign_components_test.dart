import 'package:findback/data/api_client.dart';
import 'package:findback/features/home/edit_sheet.dart';
import 'package:findback/features/home/capture_sheet.dart';
import 'package:findback/features/home/widgets/delete_toast.dart';
import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/capture_service.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

SearchResult memory(
        {String summary = 'A useful brief.', String status = 'ready'}) =>
    SearchResult.fromItem(ItemDetail.fromJson({
      'id': 'memory',
      'url': 'https://example.test',
      'title': 'A memory',
      'summary': summary,
      'status': status,
      'content_type': 'video',
      'failure_reason': 'This page requires access or sign-in.'
    }));

Widget app(Widget child, {Brightness brightness = Brightness.light}) =>
    MaterialApp(
        theme: FindBackTheme.build(brightness), home: Scaffold(body: child));

void main() {
  setUpAll(() async {
    final icons = FontLoader('MaterialIcons')
      ..addFont(rootBundle.load('fonts/MaterialIcons-Regular.otf'));
    await icons.load();
    final arabic = FontLoader('Noto Sans Arabic')
      ..addFont(rootBundle.load('assets/fonts/noto-sans-arabic/NotoSansArabic.ttf'));
    await arabic.load();
    for (final font in ['Schibsted Grotesk', 'Source Serif 4']) {
      final loader = FontLoader(font)
        ..addFont(
            rootBundle.load('assets/fonts/${font.replaceAll(' ', '')}.ttf'));
      await loader.load();
    }
  });
  test('editorial and reprocessing flags survive caching and search parsing',
      () {
    final item = ItemDetail.fromJson({
      'id': 'flags',
      'url': 'https://example.test',
      'status': 'ready',
      'edited': true,
      'description_only': true,
      'reprocessing': true,
      'reprocess_failure': 'Reader unavailable',
    });
    final cached = ItemDetail.fromLocalRow(item.toLocalRow());
    expect(
        cached.edited && cached.descriptionOnly && cached.reprocessing, isTrue);
    expect(cached.isGeneratingBrief, isTrue);
    expect(cached.reprocessFailure, 'Reader unavailable');
    final result = SearchResult.fromJson({
      'item_id': 'flags',
      'url': 'https://example.test',
      'edited': true,
      'description_only': true,
      'reprocessing': true,
      'reprocess_failure': 'Reader unavailable',
      'matched_terms': ['recipe'],
    }).copyWith();
    expect(
        result.edited && result.descriptionOnly && result.reprocessing, isTrue);
    expect(result.matchedTerms, ['recipe']);
    expect(result.reprocessFailure, cached.reprocessFailure);
  });

  testWidgets('local queued copy, description notice and menu action order',
      (tester) async {
    await tester.pumpWidget(app(ResultCard(
        result: const SearchResult(
            id: 'local-queued',
            summary: '',
            tags: [],
            category: 'other',
            score: 1,
            title: 'Queued',
            isGeneratingBrief: true),
        onTap: () {})));
    expect(
        find.text('Just saved · reading it now'),
        findsOneWidget);
    var deleted = false;
    await tester.pumpWidget(app(ResultCard(
        result: const SearchResult(
            id: 'ready',
            summary: '',
            tags: [],
            category: 'other',
            score: 1,
            title: 'Ready',
            descriptionOnly: true, matchedTerms: ['recipe']),
        onTap: () {},
        onEdit: () {},
        onDelete: () => deleted = true)));
    expect(find.text('Based on the page description only'), findsOneWidget);
    expect(find.text('Matched: recipe'), findsOneWidget);
    final actions = tester
        .widgetList<Semantics>(find.byType(Semantics))
        .map((widget) => widget.properties.customSemanticsActions)
        .where((actions) => actions != null && actions.isNotEmpty)
        .first!;
    expect(actions.keys.map((action) => action.label).toList(),
        ['Edit', 'Delete']);
    await tester.longPress(find.text('Ready'));
    await tester.pumpAndSettle();
    final labels = tester
        .widgetList<MenuItemButton>(find.byType(MenuItemButton))
        .map((button) => (button.child as Text).data)
        .toList();
    expect(labels, ['Edit', 'Delete']);
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    expect(deleted, isTrue);
  });

  testWidgets('Arabic card follows RTL at320dp and200percent', (tester) async {
    tester.view.physicalSize = const Size(320, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(Brightness.dark),
        home: Scaffold(
            body: Directionality(
                textDirection: TextDirection.rtl,
                child: MediaQuery(
                    data:
                        const MediaQueryData(textScaler: TextScaler.linear(2)),
                    child: ListView(children: [
                      ResultCard(
                          result: const SearchResult(
                              id: 'arabic',
                              title: 'أفكار تستحق العودة',
                              summary: 'ملخص الذاكرة المحفوظة',
                              tags: [],
                              category: 'other',
                              score: 1,
                              descriptionOnly: true),
                          onTap: () {},
                          onEdit: () {},

                          onDelete: () {})
                    ]))))));
    expect(tester.takeException(), isNull);
    expect(find.text('أفكار تستحق العودة'), findsOneWidget);
  });

  test('failure and link-only fields survive the device cache', () {
    final item = ItemDetail.fromJson({
      'id': 'x',
      'url': 'https://example.test',
      'status': 'ready',
      'needs_retry': true,
      'brief_source': 'fallback',
      'failure_reason': 'Cannot read this page',
      'link_only': true
    });
    final cached = ItemDetail.fromLocalRow(item.toLocalRow());
    expect(cached.failureReason, item.failureReason);
    expect(cached.linkOnly, isTrue);
    expect(cached.isGeneratingBrief, isFalse);
    expect(SearchResult.fromItem(cached).failureReason, item.failureReason);
  });

  testWidgets('summary expansion reveals all lines without opening the memory',
      (tester) async {
    var opened = false;
    final result = memory(
        summary:
            'First point\nSecond point\nThird point\nFourth point\nFifth point');
    await tester.pumpWidget(
        app(ResultCard(result: result, onTap: () => opened = true)));
    expect(find.text('+2 more'), findsOneWidget);
    await tester.tap(find.text('+2 more'));
    await tester.pump();
    expect(find.text('Show less'), findsOneWidget);
    expect(tester.widget<Text>(find.text(result.summary)).maxLines, isNull);
    expect(opened, isFalse);
    await tester.tap(find.text('Show less'));
    await tester.pump();
    expect(tester.widget<Text>(find.text(result.summary)).maxLines, 3);
  });

  testWidgets('wrapped brief expansion respects inherited text spacing',
      (tester) async {
    tester.view.physicalSize = const Size(360, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final theme = FindBackTheme.build(Brightness.light);
    final result = memory(summary: List.filled(8, 'Remember this.').join(' '));
    await tester.pumpWidget(MaterialApp(
        theme: theme.copyWith(
            textTheme: theme.textTheme.copyWith(
                bodyMedium:
                    theme.textTheme.bodyMedium!.copyWith(letterSpacing: 8))),
        home: Scaffold(body: ResultCard(result: result, onTap: () {}))));
    final more = find.textContaining(RegExp(r'^\+\d+ more$'));
    expect(more, findsOneWidget);
    await tester.tap(more);
    await tester.pump();
    expect(tester.widget<Text>(find.text(result.summary)).maxLines, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets('failed card shows its reason and invokes real action callbacks',
      (tester) async {
    var retried = false;
    var kept = false;
    await tester.pumpWidget(app(ResultCard(
        result: memory(status: 'failed'),
        onTap: () {},
        onRetry: () => retried = true,
        onKeepLink: () => kept = true)));
    expect(find.text("Couldn't read this page"), findsOneWidget);
    expect(find.text('This page requires access or sign-in.'), findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.tap(find.text('Keep link only'));
    expect(retried && kept, isTrue);
  });

  testWidgets('Space opens actions and keyboard selection invokes Edit',
      (tester) async {
    var edited = false;
    var opened = false;
    await tester.pumpWidget(app(ResultCard(
        result: memory(),
        onTap: () => opened = true,
        onEdit: () => edited = true,
        onDelete: () {})));
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.sendKeyEvent(LogicalKeyboardKey.space);
    await tester.pumpAndSettle();
    expect(find.text('Edit'), findsOneWidget);
    await tester.tap(find.text('Edit'));
    await tester.pumpAndSettle();
    expect(edited, isTrue);
    expect(opened, isFalse);
  });

  testWidgets('long press and right click open the same actions',
      (tester) async {
    await tester.pumpWidget(app(ResultCard(
        result: memory(), onTap: () {}, onEdit: () {}, onDelete: () {})));
    await tester.longPress(find.text('A memory'));
    await tester.pumpAndSettle();
    expect(find.text('Delete'), findsOneWidget);
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A memory'), buttons: 2);
    await tester.pumpAndSettle();
    expect(find.text('Edit'), findsOneWidget);
  });

  testWidgets('Edit saves both fields and closes after success',
      (tester) async {
    Map<String, String>? saved;
    await tester.pumpWidget(app(Builder(
        builder: (context) => TextButton(
            onPressed: () => EditSheet.show(context,
                title: 'Original',
                brief: 'Old brief',
                onSave: (title, brief) async =>
                    saved = {'title': title, 'brief': brief}),
            child: const Text('Open edit')))));
    await tester.tap(find.text('Open edit'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, 'Updated');
    await tester.enterText(find.byType(TextFormField).last, 'Updated brief');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(saved, {'title': 'Updated', 'brief': 'Updated brief'});
    expect(find.text('Edit memory'), findsNothing);
  });

  testWidgets('Edit retains changes and offers retry when saving fails',
      (tester) async {
    await tester.pumpWidget(app(EditSheet(
        title: 'Original',
        brief: 'Brief',
        onSave: (title, brief) async => throw ApiException('No connection'))));
    await tester.enterText(find.byType(TextFormField).first, 'Changed');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(find.text('No connection'), findsOneWidget);
    expect(find.text('Changed'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, 'Save'))
            .onPressed,
        isNotNull);
  });

  testWidgets('Cancel closes the Save sheet without capturing anything',
      (tester) async {
    var captures = 0;
    final capture = CaptureService(
        isOnline: () async => false,
        ingest: (url, preview, hint) async => throw StateError('must queue'),
        queue: (url, preview, hint) async {
          captures++;
          return url;
        });
    await tester.pumpWidget(app(Builder(
        builder: (context) => TextButton(
            onPressed: () => CaptureSheet.show(context, capture: capture),
            child: const Text('Open save')))));
    await tester.tap(find.text('Open save'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.byType(TextField).first, 'https://example.test');
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(find.byType(CaptureSheet), findsNothing);
    expect(captures, 0);
  });

  testWidgets('blank Edit title does not submit', (tester) async {
    var saves = 0;
    await tester.pumpWidget(app(EditSheet(
        title: 'Original',
        brief: '',
        onSave: (title, brief) async {
          saves++;
        })));
    await tester.enterText(find.byType(TextFormField).first, ' ');
    await tester.tap(find.text('Save'));
    await tester.pump();
    expect(find.text('Enter a title.'), findsOneWidget);
    expect(saves, 0);
  });

  testWidgets(
      'Undo restores once and expires after10s with accessible navigation',
      (tester) async {
    var restores = 0;
    await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(Brightness.light),
        builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(accessibleNavigation: true),
            child: child!),
        home: Scaffold(
            body: Builder(
                builder: (context) => TextButton(
                    onPressed: () => DeleteToast.show(context, undo: () async {
                          restores++;
                        }),
                    child: const Text('Delete'))))));
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    expect(find.text('Memory deleted'), findsOneWidget);
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(restores, 1);
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 5));
    expect(find.text('Undo'), findsOneWidget);
    await tester.pump(const Duration(seconds: 6));
    await tester.pumpAndSettle();
    expect(find.text('Undo'), findsNothing);
    expect(restores, 1);
  });

  testWidgets('Undo exposes restoration errors without claiming success',
      (tester) async {
    await tester.pumpWidget(app(Builder(
        builder: (context) => TextButton(
            onPressed: () => DeleteToast.show(context,
                undo: () async => throw ApiException('Undo window expired')),
            child: const Text('Delete')))));
    await tester.tap(find.text('Delete'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Undo'));
    await tester.pumpAndSettle();
    expect(find.text('Undo window expired'), findsOneWidget);
  });

  for (final brightness in Brightness.values) {
    for (final width in [360.0, 390.0]) {
      testWidgets('component reference at $width in $brightness',
          (tester) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        await tester.pumpWidget(MaterialApp(
            theme: FindBackTheme.build(brightness),
            builder: (context, child) => MediaQuery(
                data: MediaQuery.of(context).copyWith(disableAnimations: true),
                child: child!),
            home: Scaffold(
                body: ListView(children: [
              ResultCard(result: memory(status: 'pending'), onTap: () {}),
              ResultCard(
                  result: memory(status: 'failed'),
                  onTap: () {},
                  onRetry: () {},
                  onKeepLink: () {}),
              ResultCard(
                  result: memory(
                      summary:
                          'First point\nSecond point\nThird point\nFourth point\nFifth point'),
                  onTap: () {},
                  onEdit: () {},
                  onDelete: () {}),
              ResultCard(
                  result: SearchResult.fromItem(ItemDetail.fromJson({
                    'id': 'recipe',
                    'url': 'https://example.test',
                    'title': 'Recipe memory',
                    'status': 'ready',
                    'content_type': 'recipe',
                    'summary': 'A short recipe brief.',
                    'best_takeaway': 'A note from the saved page.',
                    'entities': {
                      'numbers': ['25 min', '4 servings']
                    }
                  })),
                  onTap: () {}),
            ]))));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        if (const bool.fromEnvironment('CAPTURE_REDESIGN')) {
          await expectLater(
              find.byType(Scaffold),
              matchesGoldenFile(
                  '/tmp/findback-components-${brightness.name}-${width.toInt()}.png'));
        }
      });
      testWidgets('sheets fit $width in $brightness at large text',
          (tester) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final capture = CaptureService(
            isOnline: () async => false,
            ingest: (url, preview, hint) async =>
                throw StateError('must queue'),
            queue: (url, preview, hint) async => url);
        await tester.pumpWidget(RepaintBoundary(
            key: const ValueKey('sheet-preview'),
            child: MaterialApp(
                theme: FindBackTheme.build(brightness),
                builder: (context, child) => MediaQuery(
                    data: MediaQuery.of(context)
                        .copyWith(textScaler: const TextScaler.linear(2)),
                    child: child!),
                home: Scaffold(
                    body: Builder(
                        builder: (context) => Column(children: [
                              TextButton(
                                  onPressed: () => CaptureSheet.show(context,
                                      capture: capture),
                                  child: const Text('Open save')),
                              TextButton(
                                  onPressed: () => EditSheet.show(context,
                                      title: 'A memory',
                                      brief: 'A useful brief.',
                                      onSave: (title, brief) async {}),
                                  child: const Text('Open edit')),
                            ]))))));
        for (final sheet in ['save', 'edit']) {
          await tester.tap(find.text('Open $sheet'));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
          await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
          if (const bool.fromEnvironment('CAPTURE_REDESIGN')) {
            await expectLater(
                find.byKey(const ValueKey('sheet-preview')),
                matchesGoldenFile(
                    '/tmp/findback-$sheet-${brightness.name}-${width.toInt()}.png'));
          }
          await tester.tap(find.text('Cancel'));
          await tester.pumpAndSettle();
        }
      });
      testWidgets('cards fit $width in $brightness at large text',
          (tester) async {
        tester.view.physicalSize = Size(width, 1000);
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        await tester.pumpWidget(MaterialApp(
            theme: FindBackTheme.build(brightness),
            builder: (context, child) => MediaQuery(
                data: MediaQuery.of(context)
                    .copyWith(textScaler: const TextScaler.linear(2)),
                child: child!),
            home: Scaffold(
                body: ListView(children: [
              ResultCard(
                  result: memory(
                      summary:
                          'A detailed brief that remains readable on a narrow phone.'),
                  onTap: () {},
                  onEdit: () {},
                  onDelete: () {}),
              ResultCard(
                  result: memory(status: 'failed'),
                  onTap: () {},
                  onRetry: () {},
                  onKeepLink: () {}),
            ]))));
        expect(tester.takeException(), isNull);
        await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
        await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      });
    }
  }
}
