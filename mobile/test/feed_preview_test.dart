import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/features/home/widgets/memory_chip.dart';
import 'package:findback/features/home/widgets/saved_date.dart';
import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('failed cards retain source URL and expose both recovery actions',
      (tester) async {
    var retried = false, kept = false;
    final item = ItemDetail.fromJson({
      'id': 'failed',
      'url': 'https://example.com/private-post',
      'title': 'Private post',
      'status': 'failed',
      'failure_reason': 'This page requires sign-in.'
    });
    await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(Brightness.light),
        home: Scaffold(
            body: ResultCard(
                result: SearchResult.fromItem(item),
                onTap: () {},
                onRetry: () => retried = true,
                onKeepLink: () => kept = true))));
    expect(find.text("Couldn't read this page"), findsOneWidget);
    expect(find.text(item.url), findsOneWidget);
    expect(find.text(item.failureReason!), findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.tap(find.text('Keep link only'));
    expect(retried, isTrue);
    expect(kept, isTrue);
  });

  testWidgets('feed shows three brief points and expands the remaining point',
      (tester) async {
    final item = ItemDetail.fromJson({
      'id': 'ready',
      'url': 'https://example.com',
      'title': 'Useful notes',
      'status': 'ready',
      'summary': 'A general summary.',
      'content_type': 'social_post',
      'category': 'video',
      'created_at': '2026-10-09T12:00:00Z',
      'key_points': [
        'First useful idea',
        'Second useful idea',
        'Third useful idea',
        'Fourth useful idea'
      ]
    });
    await tester.pumpWidget(MaterialApp(
        theme: FindBackTheme.build(Brightness.dark),
        home: Scaffold(
            body: ResultCard(
                result: SearchResult.fromItem(item),
                onTap: () {},
                onEdit: () {},
                onDelete: () {}))));
    expect(find.text('First useful idea'), findsOneWidget);
    expect(tester.getCenter(find.byType(SavedDate)).dy,
        closeTo(tester.getCenter(find.byType(MemoryChip)).dy, 1));
    expect(tester.getRect(find.byType(MenuAnchor)).right,
        closeTo(tester.getRect(find.ancestor(of: find.byType(SavedDate), matching: find.byType(Row))).right, 1));
    expect(find.text('Fourth useful idea'), findsNothing);
    await tester.tap(find.text('+1 more'));
    await tester.pumpAndSettle();
    expect(find.text('Fourth useful idea'), findsOneWidget);
    await tester.tap(find.byTooltip('Memory actions'));
    await tester.pumpAndSettle();
    expect(find.text('Edit'), findsOneWidget);
    expect(find.text('Delete'), findsOneWidget);
    expect(find.text('Summarize again'), findsNothing);
  });
}
