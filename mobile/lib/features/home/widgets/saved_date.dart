import 'package:flutter/material.dart';

class SavedDate extends StatelessWidget {
  const SavedDate({super.key, required this.date});

  final DateTime date;

  @override
  Widget build(BuildContext context) => Text(
        'Saved ${MaterialLocalizations.of(context).formatShortDate(date.toLocal())}',
        style: Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
      );
}
