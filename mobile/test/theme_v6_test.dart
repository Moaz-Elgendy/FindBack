import 'package:findback/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('v6 dark palette matches the supplied HTML', () {
    final expected = {
      FindBackColor.background: 0xFF0C1017,
      FindBackColor.card: 0xFF141A23,
      FindBackColor.ink: 0xFFE8EDF4,
      FindBackColor.muted: 0xFF97A3B6,
      FindBackColor.line: 0xFF232C3A,
      FindBackColor.brand: 0xFF47C4B4,
      FindBackColor.soft: 0xFF15313A,
      FindBackColor.accentInk: 0xFFF5C768,
      FindBackColor.toast: 0xFF263041,
      FindBackColor.stack2: 0xFF1E5560,
      FindBackColor.stack3: 0xFF2B7B80,
      FindBackColor.recipeBackground: 0xFF3A2B0E,
      FindBackColor.recipeInk: 0xFFF5C768,
    };
    for (final entry in expected.entries) {
      expect(FindBackTheme.dark[entry.key], Color(entry.value),
          reason: entry.key.name);
    }
  });

  test('v6 light recipe badge uses the reference amber palette', () {
    expect(FindBackTheme.light[FindBackColor.recipeBackground],
        const Color(0xFFFFF0D2));
    expect(
        FindBackTheme.light[FindBackColor.recipeInk], const Color(0xFF8A5200));
  });
}
