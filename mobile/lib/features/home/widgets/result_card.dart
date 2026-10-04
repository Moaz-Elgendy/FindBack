import 'package:flutter/material.dart';

import '../../../models/search_result.dart';

/// One hit in the result list. The match reason is the product's whole promise
/// ("why did I see this?"), so it gets more room than a subtitle usually does.
class ResultCard extends StatelessWidget {
  const ResultCard({super.key, required this.result, required this.onTap});

  final SearchResult result;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      clipBehavior: Clip.antiAlias,
      child: ListTile(
        onTap: onTap,
        contentPadding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
        leading: _Thumbnail(url: result.thumbnail),
        title: Text(
          result.title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.titleSmall,
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(
                result.sourceDomain ?? '',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.outline),
              ),
              const SizedBox(height: 4),
              Text(
                result.matchReason ?? '',
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.primary),
              ),
              if (result.tags.isNotEmpty) ...<Widget>[
                const SizedBox(height: 6),
                Wrap(
                  spacing: 4,
                  runSpacing: 4,
                  children: result.tags
                      .take(3)
                      .map((String tag) => _TagChip(label: tag, category: result.category))
                      .toList(growable: false),
                ),
              ],
            ],
          ),
        ),
        trailing: const Icon(Icons.chevron_right),
      ),
    );
  }
}

class _TagChip extends StatelessWidget {
  const _TagChip({required this.label, required this.category});

  final String label;
  final String category;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: colors.secondaryContainer,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        category.isEmpty ? label : '$category · $label',
        style: Theme.of(context).textTheme.labelSmall?.copyWith(color: colors.onSecondaryContainer),
      ),
    );
  }
}

class _Thumbnail extends StatelessWidget {
  const _Thumbnail({this.url});

  final String? url;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    final Widget placeholder = Container(
      width: 48,
      height: 48,
      decoration: BoxDecoration(
        color: colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Icon(Icons.article_outlined, color: colors.outline),
    );
    final String? source = url;
    if (source == null || source.isEmpty) return placeholder;
    return ClipRRect(
      borderRadius: BorderRadius.circular(8),
      child: Image.network(
        source,
        width: 48,
        height: 48,
        fit: BoxFit.cover,
        // A dead thumbnail URL must not leave a hole in the list.
        errorBuilder: (BuildContext context, Object error, StackTrace? stack) => placeholder,
      ),
    );
  }
}
