import 'package:flutter/material.dart';

import 'package:flutter/services.dart';
import 'package:flutter/semantics.dart';

import '../../../models/search_result.dart';
import '../../../theme.dart';
import '../../../widgets/feedback.dart';
import 'memory_chip.dart';
import 'saved_date.dart';
import 'processing_border.dart';

class _OpenMenuIntent extends Intent {
  const _OpenMenuIntent();
}

class ResultCard extends StatefulWidget {
  const ResultCard({super.key, required this.result, required this.onTap,
      this.onEdit,
      this.onDelete,
      this.onRetry,
      this.onKeepLink});

  final SearchResult result;
  final VoidCallback onTap;
  final VoidCallback? onEdit, onDelete, onRetry, onKeepLink;

  @override
  State<ResultCard> createState() => _ResultCardState();
}

class _ResultCardState extends State<ResultCard> {
  final _menu = MenuController();
  bool _expanded = false;
  bool _focused = false;
  bool get _hasMenu =>
      widget.onEdit != null ||
      widget.onDelete != null;

  @override
  void didUpdateWidget(ResultCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.result.id != widget.result.id ||
        oldWidget.result.summary != widget.result.summary) {
      _expanded = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final result = widget.result;
    final theme = Theme.of(context);
    final colors = theme.brightness == Brightness.dark
        ? FindBackTheme.dark
        : FindBackTheme.light;
    final shape = RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(22),
        side: BorderSide(
            width: _focused ? 2 : 1,
            color: _focused
                ? theme.colorScheme.primary
                : colors[result.isFailed
                    ? FindBackColor.failedBorder
                    : FindBackColor.line]!));
    final type = result.contentType ??
        (result.category == 'other' ? 'link' : result.category);
    final figures = (result.entities['numbers'] is List
            ? (result.entities['numbers'] as List).whereType<String>()
            : const <String>[])
        .take(3)
        .toList();
    return FocusableActionDetector(
        includeFocusSemantics: false,
        shortcuts: const {
          SingleActivator(LogicalKeyboardKey.space): _OpenMenuIntent(),
          SingleActivator(LogicalKeyboardKey.enter): ActivateIntent()
        },
        actions: {
          _OpenMenuIntent: CallbackAction<_OpenMenuIntent>(onInvoke: (_) {
            if (_hasMenu) {
              _menu.open();
            } else {
              widget.onTap();
            }
            return null;
          }),
          ActivateIntent: CallbackAction<ActivateIntent>(onInvoke: (_) {
            widget.onTap();
            return null;
          })
        },
        onShowFocusHighlight: (value) => setState(() => _focused = value),
        child: Card.outlined(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            color: colors[result.isFailed
                ? FindBackColor.failedBackground
                : result.isGeneratingBrief
                    ? FindBackColor.readingBackground
                    : FindBackColor.card],
            shape: shape,
            clipBehavior: Clip.antiAlias,
            child: GestureDetector(
                excludeFromSemantics: true,
                onSecondaryTap: _hasMenu ? _menu.open : null,
      child: ProcessingBorder(
        active: result.isGeneratingBrief,
        shape: shape,
        child: Semantics(
                        customSemanticsActions: {
                          if (widget.onEdit != null)
                            CustomSemanticsAction(label: 'Edit'):
                                widget.onEdit!,
                          if (widget.onDelete != null)
                            CustomSemanticsAction(label: 'Delete'):
                                widget.onDelete!,
                        },
                        container: true,
                        explicitChildNodes: true,
                        label: 'Open ${result.title}',
                        button: true,
                        onTap: widget.onTap,
                        child: InkWell(
                            excludeFromSemantics: true,
                            canRequestFocus: false,
                            onTap: widget.onTap,
                            onLongPress: _hasMenu ? _menu.open : null,
                            child: Padding(
                                padding: const EdgeInsets.all(18),
                                child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      if (result.isGeneratingBrief) ...[
                                        Text(
                                            'Just saved · reading it now',
                                            style: theme.textTheme.bodySmall
                                                ?.copyWith(
                                                    color: colors[FindBackColor
                                                        .accentInk])),
                                        const SizedBox(height: 14),
                                        for (final fraction in [.7, .9, .55])
                                          Padding(
                                              padding: const EdgeInsets.only(
                                                  bottom: 8),
                                              child: FractionallySizedBox(
                                                  widthFactor: fraction,
                                                  child: Container(
                                                      height: 8,
                                                      decoration: BoxDecoration(
                                                          color: colors[
                                                              FindBackColor
                                                                  .accentSoft],
                                                          borderRadius:
                                                              BorderRadius
                                                                  .circular(
                                                                      8))))),
                                      ] else ...[
                                        Row(
                                            crossAxisAlignment:
                                                CrossAxisAlignment.center,
                                            children: [
                                              Expanded(
                                                  child: result.isFailed
                                                      ? Text(
                                                          "Couldn't read this page",
                                                          style: theme.textTheme
                                                              .bodySmall)
                                                      : Align(
                                                          alignment:
                                                              AlignmentDirectional
                                                                  .centerStart,
                                                          child: MemoryChip(
                                                              type: type,
                                                              category: result
                                                                  .category))),
                                              if (result.createdAt != null)
                                                Expanded(
                                                    child: Padding(
                                                        padding:
                                                            const EdgeInsetsDirectional
                                                                .only(
                                                                start: 8),
                                                        child: SavedDate(
                                                            date: result
                                                                .createdAt!,
                                                            relative: true))),
                                              if (_hasMenu)
                                                FindBackActionMenu(
    controller: _menu,
    actions: [
      if (widget.onEdit != null) FindBackAction(label: 'Edit', icon: Icons.edit_outlined, onPressed: widget.onEdit!),
      if (widget.onDelete != null) FindBackAction(label: 'Delete', icon: Icons.delete_outline, destructive: true, onPressed: widget.onDelete!),
    ]),
                                            ]),
                                        const SizedBox(height: 8),
                                        Text(
          result.title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w600, fontSize: 18, height: 1.25, color: theme.colorScheme.onSurface)),
                                        if (result.matchedTerms.isNotEmpty)
                                          Padding(
                                              padding:
                                                  const EdgeInsetsDirectional
                                                      .only(top: 6),
                                              child: Text(
                                                  'Matched: ${result.matchedTerms.join(', ')}',
                                                  style: theme
                                                      .textTheme.bodySmall)),
                                        if (result.descriptionOnly)
                                          Padding(
                                              padding:
                                                  const EdgeInsetsDirectional
                                                      .only(top: 6),
                                              child: Text(
                                                  'Based on the page description only',
                                                  style: theme
                                                      .textTheme.bodySmall)),
                                        const SizedBox(height: 10),
                                        if (result.isFailed) ...[
                                          if (result.url.isNotEmpty && result.url != result.title) Padding(
                                            padding: const EdgeInsets.only(top: 4, bottom: 8), child: Text(result.url,
                                              maxLines: 1, overflow: TextOverflow.ellipsis,
                                              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant))),
                                          Text(
                                              result.failureReason ??
                                                  'This page could not be read. Try again or keep the link.',
                                              style: theme.textTheme.bodyMedium
                                                  ?.copyWith(
                                                      color: theme.colorScheme
                                                          .onSurfaceVariant)),
                                          if (widget.onRetry != null ||
                                              widget.onKeepLink != null)
                                            Wrap(spacing: 8, children: [
                                              if (widget.onRetry != null)
                                                TextButton(
                                                    onPressed: widget.onRetry,
                                                    child: const Text('Retry')),
                                              if (widget.onKeepLink != null)
                                                TextButton(
                                                    onPressed:
                                                        widget.onKeepLink,
                                                    child: const Text(
                                                        'Keep link only')),
                                            ]),
                                        ] else ...[
                                          if ((type == 'recipe' ||
                                                  type == 'product') &&
                                              figures.isNotEmpty) ...[
                                            Wrap(
                                                spacing: 8,
                                                runSpacing: 8,
                                                children: figures
                                                    .map((value) => Chip(
                                                        label: Text(value)))
                                                    .toList()),
                                            const SizedBox(height: 10),
                                          ],
                                          if (result.keyPoints.isNotEmpty && !result.edited) ...[
                                            for (final point in result.keyPoints.take(_expanded ? result.keyPoints.length : 3))
                                              Container(padding: const EdgeInsets.symmetric(vertical: 5),
                                                decoration: BoxDecoration(border: Border(top: BorderSide(color: theme.colorScheme.outlineVariant))),
                                                child: Text(point, style: FindBackTheme.summaryStyle.copyWith(fontSize: 15.5, height: 1.48))),
                                            if (result.keyPoints.length > 3) TextButton(onPressed: () => setState(() => _expanded = !_expanded),
                                              child: Text(_expanded ? 'Show less' : '+${result.keyPoints.length - 3} more')),
                                          ] else if (result.summary.isNotEmpty) _summary(theme),
                                          if ((type == 'recipe' ||
                                                  type == 'product') &&
                                              result.bestTakeaway?.isNotEmpty ==
                                                  true)
                                            Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(result.bestTakeaway!,
                    style: FindBackTheme
                                                        .summaryStyle
                                                        .copyWith(
                                                            color: theme
                                                                .colorScheme
                                                                .onSurfaceVariant))),
              ],
                                        if (result.sourceDomain?.isNotEmpty == true)
                                          Padding(
                                              padding: const EdgeInsets.only(
                                                  top: 10),
                                              child: Text(result.sourceDomain!,
                      style: theme.textTheme.bodySmall)),
                                      ],
                                    ]))))))));
  }

  Widget _summary(ThemeData theme) =>
      LayoutBuilder(builder: (context, constraints) {
        final style = DefaultTextStyle.of(context)
            .style
            .merge(FindBackTheme.summaryStyle)
            .copyWith(color: theme.colorScheme.onSurface);
        final painter = TextPainter(
            text: TextSpan(text: widget.result.summary, style: style),
            textDirection: Directionality.of(context),
            textScaler: MediaQuery.textScalerOf(context))
          ..layout(maxWidth: constraints.maxWidth);
        final hidden = painter.computeLineMetrics().length - 3;
        painter.dispose();
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(widget.result.summary,
              maxLines: _expanded ? null : 3,
              overflow: _expanded ? null : TextOverflow.ellipsis,
              style: style),
                if (hidden > 0)
            TextButton(
                onPressed: () => setState(() => _expanded = !_expanded),
                child: Text(_expanded ? 'Show less' : '+$hidden more')),
              ]);
      });
  }
