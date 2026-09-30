import 'package:findback/utils/share_text.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('extractUrlFromShareText', () {
    test('pulls the URL out of share-sheet prose', () {
      expect(
        extractUrlFromShareText(
          'Check this out: https://example.com/recipes/fennel-pasta — wow!',
        ),
        'https://example.com/recipes/fennel-pasta',
      );
    });

    test('strips punctuation and smart quotes glued to the end', () {
      expect(
        extractUrlFromShareText('“https://example.com/a/1”. Sure!'),
        'https://example.com/a/1',
      );
      expect(
        extractUrlFromShareText('https://example.com/a/1.'),
        'https://example.com/a/1',
      );
    });

    test('promotes a bare domain to https', () {
      expect(extractUrlFromShareText('example.com/x'), 'https://example.com/x');
    });

    test('returns null when there is nothing to save', () {
      expect(extractUrlFromShareText(null), isNull);
      expect(extractUrlFromShareText(''), isNull);
      expect(extractUrlFromShareText('no link in this sentence'), isNull);
    });
  });

  test('previewFromText caps length without splitting words mid-way', () {
    final preview = previewFromText('a' * 1000, maxLength: 100);
    expect(preview.length, 100);
    expect(previewFromText('short'), 'short');
  });

  test('sourceDomain drops www and lowercases the host', () {
    expect(sourceDomain('https://www.BBC.co.uk/food/x'), 'bbc.co.uk');
    expect(sourceDomain('not a url'), '');
  });

  group('deriveLocalTitle', () {
    test('prefers an explicit hint', () {
      expect(deriveLocalTitle(titleHint: '  Pasta with fennel  ', preview: 'ignored'),
          'Pasta with fennel');
    });

    test('falls back to the preview head, then the url', () {
      expect(deriveLocalTitle(preview: 'x' * 200, maxLength: 10)!.length, 10);
      expect(deriveLocalTitle(fallback: 'https://example.com'), 'https://example.com');
    });
  });
}
