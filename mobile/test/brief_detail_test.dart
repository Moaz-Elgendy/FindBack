import 'package:findback/features/home/detail_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/items_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('processing polls, then swaps in Brief and shows improvement badge', (WidgetTester tester) async {
    int calls = 0;
    final service = ItemsService(
      remoteItem: (_) async {
        calls++;
        return ItemDetail.fromJson(<String, dynamic>{
          'id': '1', 'url': 'https://facebook.com/reel/x', 'title': 'Skills',
          'status': calls == 1 ? 'processing' : 'ready',
          'instant_brief': calls == 1 ? null : 'Concrete skill facts.',
          'best_takeaway': calls == 1 ? null : 'Use the testing skill.',
          'needs_retry': calls == 2,
          'missing_info': calls == 2 ? 'Open original for remaining steps.' : null,
          'key_points_with_refs': calls == 1 ? <Object>[] : <Map<String, Object?>>[
            {'point': 'Testing skill', 'source_ref': '01:30'},
          ],
        });
      },
      localItem: (_) async => null,
      remoteRecent: (_, {String? category, String? cursor}) async => const ItemPage(items: []),
      localRecent: (_, {String? category}) async => [],
      cache: (_) async {}, remoteDelete: (_) async {},
      localDelete: (_) async => 0, dropQueued: (_) async => 0, isOnline: () async => true,
    );
    await tester.pumpWidget(MaterialApp(home: DetailPage(itemId: '1', items: service)));
    await tester.pump();
    expect(find.text('Processing...'), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(find.text('Concrete skill facts.'), findsOneWidget);
    expect(find.text('Use the testing skill.'), findsOneWidget);
    expect(find.text('Improving brief...'), findsOneWidget);
    expect(find.text('Open original for remaining steps.'), findsOneWidget);
    await tester.tap(find.text('Full Brief'));
    await tester.pumpAndSettle();
    expect(find.text('Testing skill'), findsOneWidget);
    expect(find.text('01:30'), findsOneWidget);
    expect(find.widgetWithText(TextButton, '01:30'), findsNothing);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(find.text('Improving brief...'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });
}
