import 'package:findback/features/home/detail_page.dart';
import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/services/items_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final item = ItemDetail.fromJson(<String, dynamic>{
  'id': '1',
  'url': 'https://facebook.com/reel/x',
  'title': 'Developer skills',
  'status': 'ready',
  'summary': 'Long legacy summary',
  'instant_brief': 'Short useful Brief.',
  'tags': ['claude skills'],
  'key_points_with_refs': [
    {'point': 'Use the testing skill.', 'source_ref': '00:39'},
    {'point': 'Review the result.', 'source_ref': '01:10'},
  ],
});

ItemsService service() => ItemsService(
      remoteItem: (_) async => item,
      localItem: (_) async => null,
      remoteRecent: (_, {String? category, String? cursor, Map<String, String>? filters}) async =>
          const ItemPage(items: []),
      localRecent: (_, {String? category, Map<String, String>? filters}) async => [],
      cache: (_) async {},
      remoteDelete: (_) async {},
      localDelete: (_) async => 0,
      dropQueued: (_) async => 0,
      isOnline: () async => true,
    );

void main() {
  test('recent cards prefer the short Brief', () {
    expect(SearchResult.fromItem(item).summary, 'Short useful Brief.');
  });

  testWidgets('cards show Brief without decorative icon or tags',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: ResultCard(
      result: SearchResult.fromItem(item),
      onTap: () {},
    ))));
    expect(find.text('Short useful Brief.'), findsOneWidget);
    expect(find.textContaining('claude skills'), findsNothing);
    expect(find.byIcon(Icons.article_outlined), findsNothing);
  });

  testWidgets(
      'detail exposes Full Brief, collapses tags and floats original action',
      (tester) async {
    await tester.pumpWidget(
        MaterialApp(home: DetailPage(itemId: '1', items: service())));
    await tester.pumpAndSettle();
    expect(find.text('Memory'), findsNothing);
    expect(find.text('Short useful Brief.'), findsOneWidget);
    expect(find.text('2 key points · Tap to expand'), findsOneWidget);
    expect(find.text('claude skills'), findsNothing);
    expect(find.widgetWithText(FloatingActionButton, 'Open Original'),
        findsOneWidget);
    await tester.tap(find.text('Full Brief'));
    await tester.pumpAndSettle();
    expect(find.text('2 key points · Tap to collapse'), findsOneWidget);
    expect(find.text('Use the testing skill.'), findsOneWidget);
    expect(find.text('00:39'), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Tags'), 150,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('Tags'));
    await tester.pumpAndSettle();
    expect(find.text('claude skills'), findsOneWidget);
    await tester.drag(find.byType(ListView), const Offset(0, -600));
    await tester.pumpAndSettle();
    expect(find.text('Open Original').hitTestable(), findsOneWidget);
    await tester.scrollUntilVisible(find.text('Full Brief'), -150,
        scrollable: find.byType(Scrollable).first);
    await tester.drag(find.byType(ListView), const Offset(0, 600));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Full Brief'));
    await tester.pumpAndSettle();
    expect(find.text('Use the testing skill.'), findsNothing);
  });

  testWidgets('expanded detail fits narrow screens with enlarged text',
      (tester) async {
    tester.view.physicalSize = const Size(320, 700);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: const TextScaler.linear(1.8)),
        child: child!,
      ),
      home: DetailPage(itemId: '1', items: service()),
    ));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Full Brief'), -150,
        scrollable: find.byType(Scrollable).first);
    await tester.drag(find.byType(ListView), const Offset(0, 600));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Full Brief'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(find.text('Tags'), 150,
        scrollable: find.byType(Scrollable).first);
    await tester.tap(find.text('Tags'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Open Original').hitTestable(), findsOneWidget);
  });
}
