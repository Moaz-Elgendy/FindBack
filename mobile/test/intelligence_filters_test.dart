import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/data/local_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  test('offline intelligence filtering happens before the row limit', () async {
    sqfliteFfiInit();
    databaseFactory = databaseFactoryFfi;
    final db = await LocalDb.openAt(inMemoryDatabasePath);
    try {
      await db.upsertRemoteItems([for (var index = 0; index < 31; index++) ItemDetail.fromJson({
        'id': '$index', 'url': 'https://example.com/$index', 'title': 'Claude tools',
        'status': 'ready', 'topics': [index == 30 ? 'AI' : 'Food'],
        'created_at': index == 30 ? '2026-09-28T00:00:00Z' : '2026-09-29T00:00:00Z',
      })]);
      expect((await db.recentLocalItems(limit: 1, filters: {'topic': 'AI'})).map((row) => row.id), ['30']);
      expect((await db.localSearch('Claude', limit: 1, filters: {'topic': 'AI'})).map((row) => row.id), ['30']);
    } finally {
      await db.close();
    }
  });

  test('intelligence survives the offline mirror and combined filters', () {
    final item = ItemDetail.fromJson({
      'id': '1', 'url': 'https://youtube.com/watch?v=x', 'status': 'ready',
      'topics': ['AI'], 'content_type': 'ai_tool',
      'entities': {'tools_products': ['Claude', 'Gamma']},
      'likely_intent': 'Try later', 'suggested_action': 'Test tool',
      'source_domain': 'youtube.com', 'created_at': '2026-09-28T10:00:00Z',
    });
    final cached = SearchResult.fromLocalRow(item.toLocalRow());
    expect(matchesIntelligence(cached, {'topic': 'AI', 'type': 'ai_tool',
      'entity': 'Claude', 'intent': 'Try later', 'action': 'Test tool',
      'source': 'youtube.com', 'saved': '2026-09-28'}), isTrue);
    expect(matchesIntelligence(cached, {'topic': 'Food'}), isFalse);
    expect(matchesIntelligence(cached, {'entity': 'Clau'}), isFalse);
    expect(matchesIntelligence(cached, {'saved': '2026-09-29'}), isFalse);
  });
}
