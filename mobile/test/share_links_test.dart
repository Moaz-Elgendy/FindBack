import 'package:flutter_test/flutter_test.dart';
import 'package:findback/config.dart';
import 'package:findback/services/share_links.dart';

void main() {
  test('share token accepts only exact configured origin and opaque path', () {
    final token = List.filled(43, 'a').join();
    final origin = Uri.parse(AppConfig.apiBaseUrl).origin;
    expect(shareToken('$origin/s/$token'), token);
    for (final value in [
      'https://evil.test/s/$token', '$origin/s/short',
      '$origin/s/$token?identity=secret', '$origin/s/$token#fragment',
      '$origin/s/$token/extra',
    ]) {
      expect(shareToken(value), isNull);
    }
  });
}
