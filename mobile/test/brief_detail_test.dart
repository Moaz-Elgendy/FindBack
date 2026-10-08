import 'package:findback/features/home/detail_page.dart';
import 'package:findback/models/item.dart';
import 'package:findback/services/items_service.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  for (final status in ['processing', 'failed']) {
    testWidgets('stored LLM Brief stays final when later processing is $status', (tester) async {
      int calls = 0;
      final service = ItemsService(
        remoteItem: (_) async {
          calls++;
          return ItemDetail.fromJson({
            'id': 'final', 'url': 'https://facebook.com/reel/final', 'title': 'Final skill facts',
            'status': status, 'brief_source': 'llm', 'needs_retry': true,
            'created_at': '2026-09-28T12:00:00Z', 'instant_brief': 'Use reusable skills to plan and test changes.',
          });
        },
        localItem: (_) async => null,
        remoteRecent: (_, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []),
        localRecent: (_, {String? category, Map<String, String>? filters}) async => [],
        cache: (_) async {}, remoteDelete: (_) async {},
        localDelete: (_) async => 0, dropQueued: (_) async => 0, isOnline: () async => true,
      );
      await tester.pumpWidget(MaterialApp(home: DetailPage(itemId: 'final', items: service)));
      await tester.pump();
      expect(find.text('Use reusable skills to plan and test changes.'), findsOneWidget);
    final title = tester.widget<Text>(find.text('Final skill facts'));
    expect(title.style!.color, Theme.of(tester.element(find.text('Final skill facts'))).colorScheme.onSurface);
    expect(find.textContaining('Saved '), findsOneWidget);
      expect(find.text('Just saved · reading it now'), findsNothing);
      expect(find.textContaining("couldn't read this one"), findsNothing);
      await tester.pump(const Duration(seconds: 30));
      expect(calls, 1);
      await tester.pumpWidget(const SizedBox());
    });

  }

  testWidgets('generated Brief hides loading during media retries and keeps limitations in Full Brief', (tester) async {
    int calls = 0;
    final service = ItemsService(
      remoteItem: (_) async {
        calls++;
        return ItemDetail.fromJson({
          'id': '2', 'url': 'https://facebook.com/post/x', 'title': 'Known facts',
          'status': 'ready', 'instant_brief': 'A tool organizes saved links.',
          'brief_source': 'llm', 'needs_retry': true,
          'missing_info': 'More details could not be collected because the caption is missing.',
        });
      },
      localItem: (_) async => null,
      remoteRecent: (_, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []),
      localRecent: (_, {String? category, Map<String, String>? filters}) async => [],
      cache: (_) async {}, remoteDelete: (_) async {},
      localDelete: (_) async => 0, dropQueued: (_) async => 0, isOnline: () async => true,
    );
    await tester.pumpWidget(MaterialApp(home: DetailPage(itemId: '2', items: service)));
    await tester.pump();
    expect(find.text('Just saved · reading it now'), findsNothing);
    expect(find.textContaining('caption is missing'), findsOneWidget);
    expect(find.text('A tool organizes saved links.'), findsOneWidget);
    expect(find.textContaining('caption is missing'), findsOneWidget);
    await tester.pump(const Duration(seconds: 30));
    expect(calls, 1);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('processing polls, then swaps in Brief and stops generating and polling after success', (WidgetTester tester) async {
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
          'brief_source': calls == 2 ? 'fallback' : 'llm',
          'missing_info': calls == 2 ? 'Open original for remaining steps.' : null,
          'key_points_with_refs': calls == 1 ? <Object>[] : <Map<String, Object?>>[
            {'point': 'Testing skill', 'source_ref': '01:30'},
          ],
        });
      },
      localItem: (_) async => null,
      remoteRecent: (_, {String? category, String? cursor, Map<String, String>? filters}) async => const ItemPage(items: []),
      localRecent: (_, {String? category, Map<String, String>? filters}) async => [],
      cache: (_) async {}, remoteDelete: (_) async {},
      localDelete: (_) async => 0, dropQueued: (_) async => 0, isOnline: () async => true,
    );
    await tester.pumpWidget(MaterialApp(home: DetailPage(itemId: '1', items: service)));
    await tester.pump();
    expect(find.text('Just saved · reading it now'), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(find.text('Concrete skill facts.'), findsOneWidget);
    expect(find.text('Use the testing skill.'), findsOneWidget);
    expect(find.text('Just saved · reading it now'), findsOneWidget);
    expect(find.text('Open original for remaining steps.'), findsOneWidget);
    expect(find.text('Testing skill'), findsOneWidget);
    expect(find.text('Open original for remaining steps.'), findsOneWidget);
    expect(find.text('01:30'), findsOneWidget);
    expect(find.widgetWithText(TextButton, '01:30'), findsNothing);
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(find.text('Just saved · reading it now'), findsNothing);
    await tester.pump(const Duration(seconds: 30));
    expect(calls, 3);
    await tester.pumpWidget(const SizedBox());
  });
}
