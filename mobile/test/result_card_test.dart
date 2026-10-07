import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final results = <String, SearchResult>{
    'offline': SearchResult.fromLocalRow({
      'id': 'saved-1', 'url': 'https://facebook.com/reel/x',
      'title': 'Claude skills', 'summary': 'Tools for planning and testing.',
      'source_domain': 'facebook.com',
    }),
    'online': SearchResult.fromJson({
      'id': 'saved-2', 'title': 'Claude skills',
      'summary': 'Tools for planning and testing.', 'source_domain': 'facebook.com',
      'match_reason': 'Matched title/summary',
    }),
  };
  for (final entry in results.entries) {
    testWidgets('${entry.key} card shows content and domain without search diagnostics', (tester) async {
      var opened = false;
      await tester.pumpWidget(MaterialApp(home: Scaffold(body: ResultCard(
        result: entry.value, onTap: () => opened = true,
      ))));
      expect(find.text('Claude skills'), findsOneWidget);
      expect(find.text('Tools for planning and testing.'), findsOneWidget);
      expect(find.text('facebook.com'), findsOneWidget);
      expect(find.text(entry.value.matchReason!), findsNothing);
      await tester.tap(find.text('Claude skills'));
      expect(opened, isTrue);
    });
  }
}
