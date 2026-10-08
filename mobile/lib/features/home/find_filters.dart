import '../../models/search_result.dart';

class FindFilter {
  const FindFilter(this.label, this.key, this.value);
  final String label, key, value;
}

List<FindFilter> findFilters(List<SearchResult> items, DateTime now) {
  final cutoff = now.toUtc().subtract(const Duration(days: 14));
  final topics = <String, int>{};
  for (final item in items) {
    for (final topic in groupedTopics(item).toSet()) {
      topics.update(topic, (count) => count + 1, ifAbsent: () => 1);
    }
  }
  final ranked = topics.keys.toList()..sort((a, b) {
    final frequency = topics[b]!.compareTo(topics[a]!);
    return frequency != 0 ? frequency : topicLabels.indexOf(a).compareTo(topicLabels.indexOf(b));
  });
  return [
    for (final (type, label) in [('video', 'a video'), ('recipe', 'a recipe'), ('product', 'something to buy')])
      if (items.any((item) => item.contentType == type)) FindFilter(label, 'type', type),
    if (items.any((item) => item.createdAt != null && !item.createdAt!.isBefore(cutoff)))
      FindFilter('last 2 weeks', 'saved_after', cutoff.toIso8601String()),
    for (final topic in ranked.take(2)) FindFilter('about $topic', 'topic', topic),
  ];
}
