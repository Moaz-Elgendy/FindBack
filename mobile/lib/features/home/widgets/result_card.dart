import 'package:flutter/material.dart';

import '../../../models/search_result.dart';
import 'saved_date.dart';
import 'processing_border.dart';

class ResultCard extends StatelessWidget {
  const ResultCard({super.key, required this.result, required this.onTap});

  final SearchResult result;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card.outlined(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      clipBehavior: Clip.antiAlias,
      child: ProcessingBorder(
        active: result.isGeneratingBrief,
        shape: theme.cardTheme.shape ?? const RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(12))),
        child: ListTile(
        onTap: onTap,
        contentPadding: const EdgeInsets.all(16),
        title: Text(
          result.title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w600, color: theme.colorScheme.primary),
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              if (result.summary.isNotEmpty) ...<Widget>[
                Text(result.summary,
                    maxLines: 4,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodyLarge?.copyWith(height: 1.5)),
                const SizedBox(height: 10),
              ],
              Wrap(spacing: 12, runSpacing: 4, children: [
                if (result.sourceDomain?.isNotEmpty == true)
                  Text(result.sourceDomain!,
                      style: theme.textTheme.bodySmall
                          ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
                if (result.createdAt != null) SavedDate(date: result.createdAt!),
              ]),
            ],
          ),
        ),
        trailing: const Icon(Icons.chevron_right),
        ),
      ),
    );
  }
}
