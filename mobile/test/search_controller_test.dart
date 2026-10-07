import 'dart:async';

import 'package:findback/data/api_client.dart';
import 'package:findback/features/home/search_controller.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';

SearchResult _hit(String id, {double score = 0.9}) => SearchResult(
      id: id,
      title: 'Hit $id',
      summary: '',
      tags: const <String>[],
      category: 'article',
      score: score,
    );

SearchResponse _response(List<String> ids, {int tookMs = 7}) => SearchResponse(
      results: ids.map((String id) => _hit(id)).toList(growable: false),
      tookMs: tookMs,
    );

void main() {
  test('a failed connectivity probe uses filtered cached results and stops loading', () async {
    final controller = SearchController(
      remote: (_, __, filters) async => throw StateError('must use cache'),
      local: (_, {String? category, Map<String, String>? filters}) async => [
        const SearchResult(id: 'ai', title: 'Tools', summary: '', tags: [], category: 'other', score: 1, topics: ['AI']),
        const SearchResult(id: 'food', title: 'Tools', summary: '', tags: [], category: 'other', score: 1, topics: ['Food']),
      ].where((item) => matchesIntelligence(item, filters ?? {})).toList(),
      isOnline: () async => throw StateError('connectivity unavailable'),
    );
    controller.filters = {'topic': 'AI'};
    await controller.run('tools');
    expect(controller.results.map((item) => item.id), ['ai']);
    expect(controller.loading, isFalse);
    expect(controller.offline, isTrue);
    controller.dispose();
  });

  test('keystrokes collapse into one request', () async {
    final List<String> asked = <String>[];
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async {
        asked.add(query);
        return _response(<String>['x']);
      },
      local: (String query, {String? category, Map<String, String>? filters}) async => const <SearchResult>[],
      isOnline: () async => true,
      debounce: const Duration(milliseconds: 30),
    );

    controller.onQueryChanged('fas');
    controller.onQueryChanged('fast');
    controller.onQueryChanged('fastest');
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(asked, <String>['fastest']);
    expect(controller.results.single.id, 'x');
    expect(controller.tookMs, 7);
    expect(controller.loading, isFalse);
    controller.dispose();
  });

  test('clearing the box clears the results without a request', () async {
    int asked = 0;
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async {
        asked++;
        return _response(<String>['x']);
      },
      local: (String query, {String? category, Map<String, String>? filters}) async => const <SearchResult>[],
      isOnline: () async => true,
      debounce: Duration.zero,
    );

    await controller.run('pasta');
    expect(asked, 1);
    await controller.run('');
    expect(asked, 1);
    expect(controller.results, isEmpty);
    expect(controller.tookMs, isNull);
    controller.dispose();
  });

  test('offline searches the device and says so', () async {
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async => throw AssertionError('no network'),
      local: (String query, {String? category, Map<String, String>? filters}) async => <SearchResult>[_hit('local-1')],
      isOnline: () async => false,
    );

    await controller.run('pasta');
    expect(controller.results.single.id, 'local-1');
    expect(controller.offline, isTrue);
    expect(controller.tookMs, isNull);
    expect(controller.loading, isFalse);
    controller.dispose();
  });

  test('a failed API call falls back to the mirror, clearly labelled', () async {
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async =>
          throw ApiException('502 bad gateway', statusCode: 502, kind: ApiFailureKind.server),
      local: (String query, {String? category, Map<String, String>? filters}) async => <SearchResult>[_hit('local-2', score: 0.55)],
      isOnline: () async => true,
    );

    await controller.run('pasta');
    final SearchResult row = controller.results.single;
    expect(row.matchReason, 'Offline fallback');
    expect(row.score, 0.3);
    expect(controller.offline, isTrue);
    expect(controller.tookMs, isNull);
    controller.dispose();
  });

  test('a slow response cannot overwrite a newer keystroke', () async {
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async {
        if (query == 'slow') {
          await Future<void>.delayed(const Duration(milliseconds: 40));
          return _response(<String>['stale']);
        }
        return _response(<String>['fresh']);
      },
      local: (String query, {String? category, Map<String, String>? filters}) async => const <SearchResult>[],
      isOnline: () async => true,
    );

    unawaited(controller.run('slow'));
    await controller.run('fast');
    await Future<void>.delayed(const Duration(milliseconds: 60));

    expect(controller.results.single.id, 'fresh');
    expect(controller.loading, isFalse);
    controller.dispose();
  });

  test('a broken mirror leaves the previous results on screen', () async {
    final SearchController controller = SearchController(
      remote: (String query, String? category, filters) async => _response(<String>['keep']),
      local: (String query, {String? category, Map<String, String>? filters}) async => throw StateError('database is locked'),
      isOnline: () async => true,
    );

    await controller.run('pasta');
    expect(controller.results.single.id, 'keep');

    final SearchController failing = SearchController(
      remote: (String query, String? category, filters) async => throw ApiException('gone'),
      local: (String query, {String? category, Map<String, String>? filters}) async => throw StateError('database is locked'),
      isOnline: () async => true,
    );
    await failing.run('pasta');
    expect(failing.results, isEmpty);
    expect(failing.loading, isFalse);
    controller.dispose();
    failing.dispose();
  });
}
