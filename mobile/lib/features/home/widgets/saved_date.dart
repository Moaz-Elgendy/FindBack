import 'package:flutter/material.dart';

class SavedDate extends StatelessWidget {
  const SavedDate({super.key, required this.date, this.relative = false});

  final DateTime date;
  final bool relative;

  String get _age {
    final age = DateTime.now().difference(date);
    if (age.inHours < 1) return 'just now';
    if (age.inDays < 1) return '${age.inHours} ${age.inHours == 1 ? 'hour' : 'hours'} ago';
    return '${age.inDays} ${age.inDays == 1 ? 'day' : 'days'} ago';
  }

  @override
  Widget build(BuildContext context) => Text(
        relative ? _age : 'Saved ${MaterialLocalizations.of(context).formatShortDate(date.toLocal())}',
        style: Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
      );
}
