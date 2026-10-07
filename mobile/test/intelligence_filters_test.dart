import 'package:findback/models/item.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:findback/data/local_db.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  test('topic choices use semantic labels, never keyword guesses or Other', () {
    SearchResult memory(String id, List<String> topics, {String title = '', Map<String, Object?> entities = const {}}) => SearchResult(
      id: id, title: title, summary: '', tags: [], category: 'other', score: 1, topics: topics, entities: entities);
    final memories = [
      memory('1', ['AI', 'Programming']),
      memory('2', ['AI', 'Programming'], title: 'Five Claude Code extensions'),
      memory('3', ['AI', 'Design']),
      memory('food', ['Food'], title: 'A recipe'),
      memory('gym', ['Gym']),
      memory('device', ['Electronics']),
      memory('other', ['History']),
    ];
    expect(topicChoices(memories), ['AI', 'Programming', 'Gym', 'Food', 'Electronics', 'Design', 'History']);
    expect(memories.where((row) => matchesIntelligence(row, {'topic': 'AI'})).map((row) => row.id), ['1', '2', '3']);
    expect(topicChoices([memories.first, memories.first]), ['AI', 'Programming']);
    expect(matchesIntelligence(memories.first, {'topic': 'A'}), isFalse);
    expect(matchesIntelligence(memories[3], {'topic': 'Food'}), isTrue);
    expect(matchesIntelligence(memories[4], {'topic': 'Gym'}), isTrue);
    expect(matchesIntelligence(memories[5], {'topic': 'Electronics'}), isTrue);
    expect(matchesIntelligence(memories[6], {'topic': 'History'}), isTrue);
    // A creator's name is not a subject; typed topics take precedence over incidental words.
    expect(topicChoices([memory('author', [], entities: {'people_orgs': ['Claude']})]), isEmpty);
    expect(topicChoices([memory('unclassified', [], title: 'Claude AI')]), isEmpty);
    expect(topicChoices([memory('junk', ['AI content quality', 'Other'])]), isEmpty);
    expect(topicChoices([memory('typed', ['Food'], title: 'Claude explains cooking')]), ['Food']);
    expect(topicChoices([memory('arabic', ['AI'])]), ['AI']);
  });

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
      expect(await db.localSearch('%'), isEmpty);
      expect(await db.localSearch('_'), isEmpty);
      expect(await db.localSearch("' OR 1=1; DROP TABLE items; --"), isEmpty);
      expect(await db.recentLocalItems(limit: 100), hasLength(31));
    } finally {
      await db.close();
    }
  });

  test('intelligence survives the offline mirror and combined filters', () {
    final item = ItemDetail.fromJson({
      'id': '1', 'url': 'https://youtube.com/watch?v=x', 'status': 'ready',
      'topics': ['AI'], 'content_type': 'ai_tool',
      'entities': {'tools_products': ['Claude Code Setup', 'Gamma'], 'people_orgs': ['Ada Lovelace'], 'numbers': ['42'], 'unknown': ['noise']},
      'likely_intent': 'Try later', 'suggested_action': 'Test tool',
      'source_domain': 'youtube.com', 'created_at': '2026-09-28T10:00:00Z',
    });
    final cached = SearchResult.fromLocalRow(item.toLocalRow());
    expect(matchesIntelligence(cached, {'entity': 'Claude'}), isTrue);
    expect(matchesIntelligence(cached, {'entity': 'Clau'}), isFalse);
    expect(cached.intelligence['entity'], ['Claude Code Setup', 'Gamma', 'Ada Lovelace']);
    expect(matchesIntelligence(cached, {'entity': '42'}), isFalse);
    expect(matchesIntelligence(cached, {'entity': 'noise'}), isFalse);
    expect(matchesIntelligence(cached, {'topic': 'AI', 'type': 'ai_tool',
      'entity': 'Claude', 'intent': 'Try later', 'action': 'Test tool',
      'source': 'youtube.com', 'saved': '2026-09-28'}), isTrue);
    expect(matchesIntelligence(cached, {'topic': 'Food'}), isFalse);
    expect(matchesIntelligence(cached, {'entity': 'Clau'}), isFalse);
    expect(matchesIntelligence(cached, {'saved': '2026-09-29'}), isFalse);
  });
}
