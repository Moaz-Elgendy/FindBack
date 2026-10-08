import 'package:findback/features/home/widgets/result_card.dart';
import 'package:findback/models/search_result.dart';
import 'package:findback/models/item.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('processing border stops when a final LLM Brief arrives', (tester) async {
    SearchResult memory(Map<String, dynamic> fields) => SearchResult.fromItem(
        ItemDetail.fromJson({'id': 'working', 'url': 'https://example.test/x',
          'title': 'Working memory', ...fields}));
    Future<void> show(SearchResult result) => tester.pumpWidget(MaterialApp(
        home: Scaffold(body: ResultCard(result: result, onTap: () {}))));
    await show(memory({'status': 'processing'}));
    expect(find.byKey(const ValueKey('processing-border')), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    await tester.pump(const Duration(milliseconds: 300));
    expect(tester.binding.hasScheduledFrame, isTrue);
    await show(memory({'status': 'ready', 'brief_source': 'fallback', 'instant_brief': 'Draft'}));
    expect(find.byKey(const ValueKey('processing-border')), findsOneWidget);
    await show(memory({'status': 'ready', 'needs_retry': true,
      'brief_source': 'llm', 'instant_brief': 'Final useful Brief.'}));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('processing-border')), findsNothing);
    expect(tester.binding.hasScheduledFrame, isFalse);
  });

  testWidgets('reduced motion shows a static processing border', (tester) async {
    final result = SearchResult.fromItem(ItemDetail.fromJson({
      'id': 'working', 'url': 'https://example.test/x', 'status': 'pending'}));
    await tester.pumpWidget(MaterialApp(
      builder: (context, child) => MediaQuery(data: MediaQuery.of(context)
          .copyWith(disableAnimations: true), child: child!),
      home: Scaffold(body: ResultCard(result: result, onTap: () {}))));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('processing-border')), findsOneWidget);
    expect(tester.binding.hasScheduledFrame, isFalse);
  });

  for (final brightness in Brightness.values) {
    testWidgets('relative age and ink title stay readable in $brightness', (tester) async {
      tester.view.physicalSize = const Size(320, 740);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final theme = ThemeData(colorSchemeSeed: const Color(0xFF1769AA), useMaterial3: true, brightness: brightness);
      final saved = DateTime.now().subtract(const Duration(days: 2));
      final result = SearchResult.fromJson({'id': 'dated', 'title': 'Claude skills',
        'summary': 'Plan and test code.', 'source_domain': 'facebook.com', 'created_at': saved.toIso8601String()});
      await tester.pumpWidget(MaterialApp(theme: theme,
        builder: (context, child) => MediaQuery(data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!),
        home: Scaffold(body: ResultCard(result: result, onTap: () {}))));
      final title = tester.widget<Text>(find.text('Claude skills'));
      expect(title.style!.color, theme.colorScheme.onSurface);
      expect(find.text('2 days ago'), findsOneWidget);
      final a = theme.colorScheme.onSurface.computeLuminance();
      final b = theme.colorScheme.surfaceContainerLow.computeLuminance();
      expect((a > b ? (a + .05) / (b + .05) : (b + .05) / (a + .05)), greaterThanOrEqualTo(4.5));
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('long Brief card stays readable and tappable on a narrow dark screen at double text size', (tester) async {
    tester.view.physicalSize = const Size(320, 740);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var opened = false;
    final result = SearchResult.fromJson({
      'id': 'large', 'title': 'Reusable Claude Code skills for planning, testing, and reviewing changes',
      'summary': 'Use reusable skills to guide planning and testing. Review the generated changes before adopting them in a project.',
      'source_domain': 'facebook.com', 'tags': ['claude skills'],
      'match_reason': 'Matched title/summary',
    });
    await tester.pumpWidget(MaterialApp(
      theme: ThemeData(useMaterial3: true, brightness: Brightness.dark),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(2)), child: child!),
      home: Scaffold(body: ResultCard(result: result, onTap: () => opened = true))));
    expect(find.text(result.summary), findsOneWidget);
    expect(find.text('facebook.com'), findsOneWidget);
    expect(find.text('claude skills'), findsNothing);
    expect(find.text('Matched title/summary'), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.tap(find.text(result.title));
    expect(opened, isTrue);
  });

  final results = <String, SearchResult>{
    'offline': SearchResult.fromLocalRow({
      'id': 'saved-1', 'url': 'https://facebook.com/reel/x', 'status': 'ready',
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
