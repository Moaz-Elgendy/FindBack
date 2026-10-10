import '../../widgets/feedback.dart';
import 'widgets/saved_date.dart';
import 'widgets/memory_chip.dart';
import 'widgets/delete_toast.dart';
import 'edit_sheet.dart';
import 'reminder_button.dart';
import '../../app_services.dart';
import '../../theme.dart';
import 'package:share_plus/share_plus.dart';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../../data/api_client.dart';
import '../../models/item.dart';
import '../../services/items_service.dart';

/// Port of the RN `DetailScreen`: summary, key points, ingredients, open the
/// original, copy the summary, Cook Mode, and delete.
// TODO: Remove temporary search debugging tags before public launch.
const showSearchDebugTags = true;

class DetailPage extends StatefulWidget {
  const DetailPage({super.key, required this.itemId, required this.items, this.onChanged, this.services});

  final AppServices? services;
  final String itemId;
  final ItemsService items;

  /// Lets the list behind us reload after a delete.
  final VoidCallback? onChanged;

  @override
  State<DetailPage> createState() => _DetailPageState();
}

class _DetailPageState extends State<DetailPage> {
  ItemDetail? _item;
  bool _loading = true;
  String? _error;
  bool _cookMode = false;
  AppServices? get _services => widget.services ?? AppServicesScope.maybeOf(context);
  bool _actionBusy = false;
  String? _reportedFailure;
  Timer? _refreshTimer;
  bool _openSent = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    // Never leave the screen forcing the display on.
    if (_cookMode) WakelockPlus.disable();
    super.dispose();
  }

  Future<void> _load({bool refresh = false}) async {
    _refreshTimer?.cancel();
    setState(() {
      if (!refresh) _loading = true;
      _error = null;
    });
    try {
      final ItemDetail? item = await widget.items.getItem(widget.itemId);
      if (!mounted) return;
      setState(() {
        _item = item;
        _loading = false;
      });
      if (item != null && !_openSent) {
        _openSent = true;
        // Fire-and-forget on purpose: the screen never waits on this stamp,
        // and a failure lands in the sync queue instead of an error dialog.
        unawaited(widget.items.markOpened(item));
      }
      if (item?.reprocessFailure != null && item!.reprocessFailure != _reportedFailure) {
        _reportedFailure = item.reprocessFailure;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _showSnack(item.reprocessFailure!);
        });
      }
      if (item != null && item.isGeneratingBrief) {
        _refreshTimer = Timer(const Duration(seconds: 5), () => _load(refresh: true));
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error.message;
        _loading = false;
      });
    }
  }

  Future<void> _toggleCookMode() async {
    final bool next = !_cookMode;
    await WakelockPlus.toggle(enable: next);
    if (!mounted) return;
    setState(() => _cookMode = next);
  }

  Future<void> _openOriginal(String url) async {
    final Uri? uri = Uri.tryParse(url);
    if (uri == null || !uri.hasScheme) {
      _showSnack('No usable link to open.');
      return;
    }
    try {
      final bool opened = await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!opened) _showSnack('Nothing on this device opens that link.');
    } catch (_) {
      _showSnack('Could not open the link.');
    }
  }

  Future<void> _copySummary(ItemDetail item) async {
    final points = item.pointsWithRefs.isNotEmpty ? item.pointsWithRefs :
      [for (final point in item.keyPoints) BriefKeyPoint(point: point)];
    final text = [item.bestTitle.trim(), if (item.briefText.trim().isNotEmpty) item.briefText.trim(),
      if (points.isNotEmpty) [for (final (index, point) in points.indexed)
        '${index + 1}. ${point.point.trim()}${point.sourceRef?.trim().isNotEmpty == true ? ' (${point.sourceRef!.trim()})' : ''}'].join('\n'),
      item.url].join('\n\n');
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    _showSnack('Summary copied');
  }

  void _showSnack(String message) =>
      showFindBackToast(context, message);

  Future<void> _action(String action) async {
    final item = _item;
    final services = _services;
    if (item == null || _actionBusy) return;
    if (services == null) return;
    setState(() => _actionBusy = true);
    try {
      if (action == 'Copy summary') {
        await _copySummary(item);
      } else if (action == 'Edit') {
        await EditSheet.show(context, title: item.bestTitle, brief: item.briefText,
          onSave: (title, brief) async {
            await services.actions.edit(item.id, title: title, summary: brief);
            widget.onChanged?.call();
            await _load(refresh: true);
          });
      } else if (action == 'Summarize again') {
        var replace = false;
        if (item.edited) {
          replace = await showDialog<bool>(context: context, builder: (dialog) => AlertDialog(
            title: const Text('Replace your edits with a new summary?'),
            actions: [TextButton(onPressed: () => Navigator.pop(dialog, false), child: const Text('Cancel')),
              FilledButton(onPressed: () => Navigator.pop(dialog, true), child: const Text('Replace'))])) ?? false;
          if (!replace || !mounted) return;
        }
        await services.actions.summarizeAgain(item.id, replaceEdits: replace);
        widget.onChanged?.call();
        await _load(refresh: true);
      } else if (action == 'Delete') {
        if (!await confirmMemoryDeletion(context, services.db) || !mounted) return;
        final deletion = await services.actions.delete(item.id);
        widget.onChanged?.call();
        if (!mounted) return;
        DeleteToast.show(context, localOnly: deletion.localOnly, undo: () async {
          await deletion.undo(); widget.onChanged?.call();
        });
        Navigator.pop(context);
      }
    } on ApiException catch (error) {
      if (mounted) _showSnack(error.message);
    } catch (_) {
      if (mounted) _showSnack('Could not update this memory. Try again.');
    } finally { if (mounted) setState(() => _actionBusy = false); }
  }

  Future<void> _share(ItemDetail item) async {
    final box = context.findRenderObject() as RenderBox?;
    try {
      await SharePlus.instance.share(ShareParams(text: item.url,
        sharePositionOrigin: box == null ? null : box.localToGlobal(Offset.zero) & box.size));
    } catch (_) { if (mounted) _showSnack('Could not share this link. Try again.'); }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final item = _item;
    return Scaffold(
      appBar: AppBar(leading: BackButton(onPressed: () => Navigator.maybePop(context)),
        actions: [if (item != null && _services != null) FindBackActionMenu(actions: [
    for (final action in ['Edit', 'Copy summary', 'Summarize again', 'Delete'])
      FindBackAction(label: action, icon: switch (action) { 'Edit' => Icons.edit_outlined, 'Copy summary' => Icons.copy_outlined, 'Summarize again' => Icons.refresh, _ => Icons.delete_outline }, destructive: action == 'Delete', onPressed: () => _action(action)),
  ])]),
      bottomNavigationBar: item == null || _error != null ? null : SafeArea(top: false,
        child: Padding(padding: const EdgeInsetsDirectional.fromSTEB(16, 14, 16, 24),
          child: Row(children: [
            Expanded(child: FilledButton(onPressed: () => _openOriginal(item.url),
              style: FilledButton.styleFrom(padding: const EdgeInsets.symmetric(vertical: 16),
                textStyle: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600)),
              child: const Text('Open original'))),
            const SizedBox(width: 10),
            OutlinedButton(onPressed: () => _share(item),
              style: OutlinedButton.styleFrom(backgroundColor: theme.colorScheme.surface,
                side: BorderSide(color: theme.colorScheme.outlineVariant),
                padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 16)),
              child: const Text('Share')),
          ]))),
      body: _loading ? const Center(child: CircularProgressIndicator()) : _error != null
        ? _ErrorBody(message: _error!, onRetry: _load) : item == null
        ? const _ErrorBody(message: 'That memory is not on this device yet.') : _buildDetail(theme, item),
    );
  }

  Widget _buildDetail(ThemeData theme, ItemDetail item) {
    final points = item.pointsWithRefs.isNotEmpty ? item.pointsWithRefs :
      [for (final point in item.keyPoints) BriefKeyPoint(point: point)];
    final colors = theme.brightness == Brightness.dark ? FindBackTheme.dark : FindBackTheme.light;
    return ListView(padding: const EdgeInsetsDirectional.fromSTEB(20, 12, 20, 24), children: [
      Text(item.bestTitle, style: theme.textTheme.headlineSmall?.copyWith(fontWeight: FontWeight.w600,
        height: 1.2, color: theme.colorScheme.onSurface)),
      const SizedBox(height: 12),
      Wrap(spacing: 8, runSpacing: 8, crossAxisAlignment: WrapCrossAlignment.center, children: [
        MemoryChip(type: item.contentType ?? 'link', category: item.category ?? 'other'),
        if (item.sourceDomain?.isNotEmpty == true) Text(item.sourceDomain!, style: theme.textTheme.bodySmall),
        if (item.createdAt != null) SavedDate(date: item.createdAt!),
      ]),
      if (item.descriptionOnly) Padding(padding: const EdgeInsetsDirectional.only(top: 8),
        child: Text('Based on the page description only', style: theme.textTheme.bodySmall)),
      if (item.isFailed && !item.hasFinalBrief) Padding(padding: const EdgeInsetsDirectional.only(top: 12),
        child: _Banner(text: item.failureReason ?? 'Could not read this page. Retry or keep the link.', failed: true)),
      if (item.isGeneratingBrief) const Padding(padding: EdgeInsetsDirectional.only(top: 12),
        child: _Banner(text: 'Just saved · reading it now')),
      const SizedBox(height: 20),
      Container(padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 6), decoration: BoxDecoration(
        color: colors[FindBackColor.card], borderRadius: BorderRadius.circular(22),
        border: Border.all(color: colors[FindBackColor.line]!)),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(item.briefText.isEmpty ? 'No summary yet.' : item.briefText, style: FindBackTheme.summaryStyle),
          if (item.bestTakeaway?.isNotEmpty == true) Padding(padding: const EdgeInsetsDirectional.only(top: 12),
            child: Text(item.bestTakeaway!, style: FindBackTheme.summaryStyle)),
          for (final (index, point) in points.indexed) Container(padding: const EdgeInsets.symmetric(vertical: 15),
            decoration: BoxDecoration(border: Border(top: BorderSide(color: colors[FindBackColor.line]!))),
            child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Semantics(label: 'Point ${index + 1}', child: Container(width: MediaQuery.textScalerOf(context).scale(30), height: MediaQuery.textScalerOf(context).scale(30),
                alignment: Alignment.center, decoration: BoxDecoration(color: colors[FindBackColor.soft],
                  shape: BoxShape.circle), child: Text('${index + 1}', style: TextStyle(color: colors[FindBackColor.brand], fontSize: 14, fontWeight: FontWeight.w700)))),
              const SizedBox(width: 12),
              Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(point.point, style: FindBackTheme.summaryStyle.copyWith(fontSize: 15.5, height: 1.48)),
                if (point.sourceRef != null) point.timestampUrl(item.url) == null
                  ? Text(point.sourceRef!, style: theme.textTheme.bodySmall)
                  : TextButton(onPressed: () => _openOriginal(point.timestampUrl(item.url)!), child: Text('Jump to ${point.sourceRef}')),
              ])),
            ])),
          if (item.missingInfo?.isNotEmpty == true) Padding(padding: const EdgeInsetsDirectional.only(top: 12),
            child: Text(item.missingInfo!, style: theme.textTheme.bodySmall)),
        ])),
      if (_services?.reminders != null) Padding(padding: const EdgeInsetsDirectional.only(top: 20),
        child: ReminderButton(item: item, service: _services!.reminders!)),
      if (item.ingredients.isNotEmpty) ...[
        const SizedBox(height: 20), Text('Ingredients', style: theme.textTheme.titleMedium),
        for (final ingredient in item.ingredients) _Bullet(text: ingredient),
      ],
      const SizedBox(height: 20),
      Wrap(spacing: 12, runSpacing: 8, children: [
        if (item.category == 'recipe') FilledButton.tonalIcon(onPressed: _toggleCookMode,
          icon: Icon(_cookMode ? Icons.dark_mode : Icons.restaurant), label: Text(_cookMode ? 'Exit Cook Mode' : 'Cook Mode')),
      ]),
      if (showSearchDebugTags && item.tags.isNotEmpty) ExpansionTile(title: Text('Tags', style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)), subtitle: Text('${item.tags.length} search tags'),
        children: [Wrap(spacing: 6, runSpacing: 6, children: [for (final tag in item.tags) Chip(label: Text(tag))])]),
    ]);
  }
}

class _Bullet extends StatelessWidget {
  const _Bullet({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final TextStyle? style = Theme.of(context).textTheme.bodyMedium;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('•  ', style: style),
          Expanded(child: Text(text, style: style)),
        ],
      ),
    );
  }
}

class _Banner extends StatelessWidget {
  const _Banner({required this.text, this.failed = false});

  final String text;

  /// A failed save is not "work in progress", so it is coloured differently
  /// from the still-processing note instead of reading like a live status.
  final bool failed;

  @override
  Widget build(BuildContext context) {
    final ColorScheme colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: failed ? colors.errorContainer : colors.secondaryContainer,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(
        text,
        style: Theme.of(context).textTheme.bodySmall?.copyWith(
              color: failed ? colors.onErrorContainer : colors.onSecondaryContainer,
            ),
      ),
    );
  }
}

class _ErrorBody extends StatelessWidget {
  const _ErrorBody({required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Text(message, textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodyLarge),
            if (onRetry != null) ...<Widget>[
              const SizedBox(height: 12),
              OutlinedButton(onPressed: onRetry, child: const Text('Try again')),
            ],
          ],
        ),
      ),
    );
  }
}
