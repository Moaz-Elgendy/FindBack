import 'package:flutter/material.dart';

import '../../../theme.dart';

class MemoryChip extends StatelessWidget {
  const MemoryChip({super.key, required this.type, required this.category});

  final String type, category;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).brightness == Brightness.dark
        ? FindBackTheme.dark
        : FindBackTheme.light;
    final (background, foreground) = switch (type) {
      'recipe' => (FindBackColor.recipeBackground, FindBackColor.recipeInk),
      'product' => (FindBackColor.productBackground, FindBackColor.productInk),
      _ => (FindBackColor.soft, FindBackColor.brand),
    };
    String label(String value) => value
        .replaceAll('_', ' ')
        .replaceFirstMapped(RegExp(r'^.'), (match) => match[0]!.toUpperCase());
    return Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
            color: colors[background], borderRadius: BorderRadius.circular(99)),
        child: Text(
            '${label(type)}${category != type && category != 'other' ? ' · ${label(category)}' : ''}',
            style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: colors[foreground], fontWeight: FontWeight.w600)));
  }
}
