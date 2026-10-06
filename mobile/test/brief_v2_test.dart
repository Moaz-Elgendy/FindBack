import 'package:findback/models/item.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('new Brief fields survive API parsing and the offline mirror', () {
    final item = ItemDetail.fromJson(<String, dynamic>{
      'id': '1', 'url': 'https://youtube.com/watch?v=x', 'status': 'ready',
      'summary': 'Legacy summary', 'instant_brief': 'Concrete facts', 'best_takeaway': 'Use the skill',
      'missing_info': 'Open original for details', 'needs_retry': true,
      'brief_source': 'llm',
      'key_points': <String>['One skill'],
      'key_points_with_refs': <Map<String, Object?>>[{'point': 'One skill', 'source_ref': '01:30'}],
    });
    final cached = ItemDetail.fromLocalRow(item.toLocalRow());
    expect(cached.instantBrief, 'Concrete facts');
    expect(cached.bestTakeaway, 'Use the skill');
    expect(cached.missingInfo, 'Open original for details');
    expect(cached.needsRetry, isTrue);
    expect(cached.isGeneratingBrief, isFalse);
    expect(cached.pointsWithRefs.single.sourceRef, '01:30');
    expect(cached.keyPoints, <String>['One skill']);
  });

  test('timestamp links are supported only for YouTube and preserve query parameters', () {
    const point = BriefKeyPoint(point: 'Skill', sourceRef: '01:30');
    expect(point.timestampUrl('https://youtube.com/watch?v=x&list=y'), 'https://youtube.com/watch?v=x&list=y&t=90s');
    expect(point.timestampUrl('https://youtu.be/x'), 'https://youtu.be/x?t=90s');
    expect(point.timestampUrl('https://facebook.com/reel/x'), isNull);
    expect(point.timestampUrl('https://youtube.com.evil.test/x'), isNull);
    expect(const BriefKeyPoint(point: 'Caption', sourceRef: 'caption').timestampUrl('https://youtube.com/watch?v=x'), isNull);
  });

  test('legacy items still parse without new fields', () {
    final item = ItemDetail.fromJson(<String, dynamic>{'id': '1', 'url': 'https://example.test', 'key_points': <String>['Old point']});
    expect(item.pointsWithRefs, isEmpty);
    expect(item.needsRetry, isFalse);
    expect(item.keyPoints, <String>['Old point']);
  });
}
