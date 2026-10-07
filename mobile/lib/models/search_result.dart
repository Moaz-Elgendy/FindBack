import 'item.dart';
import 'json_utils.dart';

/// One hit from `GET /api/v1/search`.
///
/// `title`/`summary` collapse to empty strings rather than null: the API
/// declares them optional, and the list UI would otherwise crash on an item
/// whose ingest never produced a title.
class SearchResult {
  const SearchResult({
    required this.id,
    required this.title,
    required this.summary,
    required this.tags,
    required this.category,
    required this.score,
    this.thumbnail,
    this.sourceDomain,
    this.matchReason,
    this.createdAt,
    this.isGeneratingBrief = false,
    this.topics = const [], this.contentType, this.entities = const {}, this.likelyIntent, this.suggestedAction, this.intent,
  });

  factory SearchResult.fromJson(Map<String, dynamic> json) => SearchResult(
        id: asId(json['id']),
        title: json['title'] as String? ?? '',
        summary: json['summary'] as String? ?? '',
        tags: stringList(json['tags']),
        category: json['category'] as String? ?? 'other',
        score: (json['score'] as num?)?.toDouble() ?? 0,
        thumbnail: json['thumbnail'] as String? ?? json['thumbnail_url'] as String?,
        sourceDomain: json['source_domain'] as String?,
        matchReason: json['match_reason'] as String?,
        createdAt: parseDate(json['created_at']),
        topics: stringList(json['topics']), contentType: json['content_type'] as String?, entities: objectMap(json['entities']),
        likelyIntent: json['likely_intent'] as String?, suggestedAction: json['suggested_action'] as String?, intent: json['intent'] as String?,
      );

  /// Rows written by the offline optimist before the server saw them.
  factory SearchResult.fromLocalRow(Map<String, Object?> row) =>
      SearchResult.fromItem(ItemDetail.fromLocalRow(row))
          .copyWith(matchReason: 'Offline — matched title/summary', score: 0.5);

  final String id;
  final String title;
  final String summary;
  final List<String> tags;
  final String category;
  final double score;
  final String? thumbnail;
  final String? sourceDomain;
  final String? matchReason;
  final DateTime? createdAt;
  final bool isGeneratingBrief;
  final List<String> topics;
  final Map<String, Object?> entities;
  final String? contentType, likelyIntent, suggestedAction, intent;

  Map<String, List<String>> get intelligence => {
    'topic': groupedTopics(this), 'type': [if (contentType != null) contentType!],
    'entity': ['tools_products', 'people_orgs'].expand((key) {
      final values = entities[key];
      return values is List ? values.whereType<String>() : <String>[];
    }).toList(),
    'intent': [if ((likelyIntent ?? intent) != null) (likelyIntent ?? intent)!],
    'action': [if (suggestedAction != null) suggestedAction!],
    'source': [if (sourceDomain != null) sourceDomain!],
    'saved': [if (createdAt != null) createdAt!.toUtc().toIso8601String().substring(0, 10)],
  };

  /// The library list ("Recent") shows cached items the same way.
  factory SearchResult.fromItem(ItemDetail item) => SearchResult(
        id: item.id,
        title: item.bestTitle,
        summary: item.briefText,
        tags: item.tags,
        category: item.category ?? 'other',
        score: 1,
        thumbnail: item.thumbnailUrl,
        sourceDomain: item.sourceDomain,
        matchReason: 'Recently saved',
        createdAt: item.createdAt,
      isGeneratingBrief: item.isGeneratingBrief,
        topics: item.topics, contentType: item.contentType, entities: item.entities,
        likelyIntent: item.likelyIntent, suggestedAction: item.suggestedAction, intent: item.intent,
      );

  bool get isLocalOnly => id.startsWith('local-');

  /// Relabels locally-derived hits (the offline fallback path marks them
  /// differently from a plain offline search).
  SearchResult copyWith({String? matchReason, double? score}) => SearchResult(
        id: id,
        title: title,
        summary: summary,
        tags: tags,
        category: category,
        score: score ?? this.score,
        thumbnail: thumbnail,
        sourceDomain: sourceDomain,
        matchReason: matchReason ?? this.matchReason,
        createdAt: createdAt,
      isGeneratingBrief: isGeneratingBrief,
        topics: topics, contentType: contentType, entities: entities,
        likelyIntent: likelyIntent, suggestedAction: suggestedAction, intent: intent,
      );
}

/// Tags are a JSON-encoded string in the SQLite mirror and a real array on the
/// wire; both shapes are handled by `decodeTags` in `json_utils.dart`.

class SearchResponse {
  const SearchResponse({required this.results, required this.tookMs});

  factory SearchResponse.fromJson(Map<String, dynamic> json) => SearchResponse(
        results: asObjectList(json['results']).map(SearchResult.fromJson).toList(growable: false),
        tookMs: (json['took_ms'] as num?)?.toInt() ?? 0,
      );

  final List<SearchResult> results;
  final int tookMs;
}


bool matchesIntelligence(SearchResult item, Map<String, String> filters) =>
    filters.entries.every((entry) {
      final knownTopic = topicLabels.any((label) => label.toLowerCase() == entry.value.toLowerCase());
      // Preserve legacy specific-topic filters without offering them in the selector.
      final values = entry.key == 'topic' && !knownTopic
          ? item.topics : item.intelligence[entry.key] ?? <String>[];
      return values.any((value) => entry.key == 'entity'
          ? RegExp(r'(^|[^\p{L}\p{N}_])' + RegExp.escape(entry.value.trim()) +
              r'($|[^\p{L}\p{N}_])', caseSensitive: false, unicode: true).hasMatch(value)
          : value.toLowerCase() == entry.value.toLowerCase());
    });

// Shared with the backend; these are LLM classifications, not keyword guesses.
const topicLabels = <String> ['AI', 'Programming', 'Gym', 'Food', 'Electronics', 'Design', 'Business', 'Education', 'Science', 'History', 'Travel', 'Health', 'Finance', 'Entertainment', 'Lifestyle', 'Culture'];

List<String> groupedTopics(SearchResult item) => item.topics.map((topic) =>
    topicLabels.where((label) => label.toLowerCase() == topic.trim().toLowerCase()).firstOrNull)
    .whereType<String>().toSet().toList();

List<String> topicChoices(Iterable<SearchResult> items) {
  final available = items.expand(groupedTopics).toSet();
  return topicLabels.where(available.contains).toList();
}
