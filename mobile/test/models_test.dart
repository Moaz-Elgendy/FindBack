import 'package:findback/models/item.dart';
import 'package:findback/models/json_utils.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('SearchResult tolerates the optional fields the API omits', () {
    final result = SearchResult.fromJson(const <String, dynamic>{'id': '7'});
    expect(result.id, '7');
    expect(result.title, '');
    expect(result.summary, '');
    expect(result.tags, isEmpty);
    expect(result.category, 'other');
    expect(result.score, 0);
    expect(result.createdAt, isNull);
  });

  test('SearchResult reads both thumbnail spellings the API has used', () {
    expect(
      SearchResult.fromJson(const <String, dynamic>{'thumbnail': 'a'}).thumbnail,
      'a',
    );
    expect(
      SearchResult.fromJson(const <String, dynamic>{'thumbnail_url': 'b'}).thumbnail,
      'b',
    );
  });

  test('local rows are recognisable as not-yet-uploaded saves', () {
    final row = SearchResult.fromLocalRow(const <String, Object?>{
      'id': 'local-123',
      'url': 'https://example.com/a',
      'title_clean': 'Fennel pasta',
      'tags': '["dinner","italian"]',
      'created_at': '2026-09-29T10:00:00.000Z',
    });
    expect(row.isLocalOnly, isTrue);
    expect(row.title, 'Fennel pasta');
    expect(row.tags, <String>['dinner', 'italian']);
    expect(row.matchReason, contains('Offline'));
    expect(row.createdAt, isNotNull);
  });

  test('corrupt tag blobs degrade to no tags instead of throwing', () {
    expect(decodeTags('{not json'), isEmpty);
    expect(decodeTags(null), isEmpty);
    expect(decodeTags(<String>['ok']), <String>['ok']);
  });

  test('SyncBatchResult pairs client ids with the server ids they became', () {
    final result = SyncBatchResult.fromJson(const <String, dynamic>{
      'mapped': <Map<String, Object?>>[
        <String, Object?>{'client_id': 'c1', 'id': 'uuid-1'},
        <String, Object?>{'client_id': 'c2', 'id': 'uuid-2'},
        <String, Object?>{'client_id': null, 'id': 'uuid-3'},
      ],
      'errors': <Map<String, Object?>>[
        <String, Object?>{'client_id': 'c4', 'reason': 'invalid url'},
      ],
    });
    expect(result.mapped.map((MappedSave m) => m.clientId), <String>['c1', 'c2']);
    expect(result.mapped.first.serverId, 'uuid-1');
    expect(result.failedClientIds, <String>['c4']);
    expect(result.isEmpty, isFalse);
  });

  test('ItemDetail round-trips through the local mirror shape', () {
    final ItemDetail item = ItemDetail.fromJson(const <String, dynamic>{
      'id': 'uuid-9',
      'url': 'https://example.com/recipe',
      'title': 'Recipe | Site',
      'title_clean': 'Recipe',
      'summary': 'A summary',
      'category': 'recipe',
      'tags': <String>['dinner'],
      'key_points': <String> ['step one'],
      'entities': <String, Object?>{'ingredients': <String>['fennel']},
      'source_domain': 'example.com',
      'status': 'ready',
      'created_at': '2026-09-29T10:00:00.000Z',
    });
    expect(item.isReady, isTrue);
    expect(item.bestTitle, 'Recipe');
    expect(item.ingredients, <String>['fennel']);
    // The detail payload has no thumbnail; the mirror stores none.
    final Map<String, Object?> row = item.toLocalRow();
    final ItemDetail copy = ItemDetail.fromLocalRow(row);
    expect(copy.id, item.id);
    expect(copy.url, item.url);
    expect(copy.tags, item.tags);
    expect(copy.summary, item.summary);
    expect(copy.status, 'ready');
    expect(copy.bestTitle, 'Recipe');
  });

  test('a canonical_url-less payload falls back to the submitted url', () {
    final ItemDetail item = ItemDetail.fromJson(const <String, dynamic>{
      'id': 'x',
      'url': 'https://example.com/a',
    });
    expect(item.canonicalUrl, 'https://example.com/a');
    expect(item.bestTitle, 'https://example.com/a');
  });

  test('ItemPage keeps its opaque cursor for the next request', () {
    final ItemPage page = ItemPage.fromJson(const <String, dynamic>{
      'items': <Map<String, Object?>>[<String, Object?>{'id': 'a', 'url': 'https://a'}],
      'next_cursor': 'eyJvZmZzZXQiOjF9',
    });
    expect(page.items.single.id, 'a');
    expect(page.nextCursor, 'eyJvZmZzZXQiOjF9');
  });
}
