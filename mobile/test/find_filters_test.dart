import 'package:findback/features/home/find_filters.dart';
import 'package:findback/models/search_result.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  final now = DateTime.utc(2026, 10, 8);
  SearchResult memory(String id, String type, List<String> topics, {DateTime? saved}) => SearchResult(
    id: id, title: id, summary: '', tags: [], category: type, contentType: type,
    topics: topics, score: 1, createdAt: saved ?? now);
  test('chips derive two most used topics and hide empty standalone filters', () {
    final items = [memory('1', 'video', ['AI']), memory('2', 'video', ['AI']),
      memory('3', 'recipe', ['Food']), memory('4', 'article', ['History'])];
    final chips = findFilters(items, now);
    expect(chips.map((chip) => chip.label), ['a video', 'a recipe', 'last 2 weeks', 'about AI', 'about Food']);
    expect(chips.map((chip) => (chip.key, chip.value)).toSet().length, chips.length);
    expect(findFilters([], now), isEmpty);
    expect(findFilters([memory('old', 'product', ['Design'], saved: now.subtract(const Duration(days: 15)))], now)
      .map((chip) => chip.label), ['something to buy', 'about Design']);
  });
  test('six-chip maximum and inclusive 14-day cutoff', () {
    final chips = findFilters([memory('v', 'video', ['AI']), memory('r', 'recipe', ['Food']),
      memory('p', 'product', ['Design'], saved: now.subtract(const Duration(days: 14)))], now);
    expect(chips, hasLength(6));
    expect(chips.singleWhere((chip) => chip.key == 'saved_after').value, '2026-09-24T00:00:00.000Z');
  });
}
