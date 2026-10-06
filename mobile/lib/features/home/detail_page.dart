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
class DetailPage extends StatefulWidget {
  const DetailPage({super.key, required this.itemId, required this.items, this.onChanged});

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
  Timer? _refreshTimer;

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
      if (item != null && ((!item.isReady && !item.isFailed) || item.needsRetry)) {
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
    await Clipboard.setData(ClipboardData(text: item.briefText));
    if (!mounted) return;
    _showSnack('Summary copied.');
  }

  void _showSnack(String message) =>
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));

  Future<void> _confirmDelete(ItemDetail item) async {
    final bool? confirmed = await showDialog<bool>(
      context: context,
      builder: (BuildContext context) => AlertDialog(
        title: const Text('Delete memory?'),
        content: const Text('This cannot be undone.'),
        actions: <Widget>[
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: Theme.of(context).colorScheme.error),
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;

    // Capture the messenger before popping: after pop this route's element is
    // deactivated and looking up an ancestor from it throws.
    final ScaffoldMessengerState messenger = ScaffoldMessenger.of(context);
    try {
      final DeleteOutcome outcome = await widget.items.remove(item.id);
      widget.onChanged?.call();
      if (!mounted) return;
      Navigator.pop(context);
      if (outcome.needsNetwork) {
        messenger.showSnackBar(
          const SnackBar(content: Text('Removed from this device. The server copy returns until you reconnect.')),
        );
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      _showSnack('Delete failed: ${error.message}');
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(),
      floatingActionButton: !_loading && _error == null && _item != null
          ? FloatingActionButton.extended(
              onPressed: () => _openOriginal(_item!.url),
              icon: const Icon(Icons.open_in_new),
              label: const Text('Open Original'),
            )
          : null,
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _ErrorBody(message: _error!, onRetry: _load)
              : _item == null
                  ? const _ErrorBody(message: 'That memory is not on this device yet.')
                  : _buildDetail(theme, _item!),
    );
  }

  Widget _buildDetail(ThemeData theme, ItemDetail item) {
    final List<String> ingredients = item.ingredients;
    return ListView(
      padding: const EdgeInsets.fromLTRB(20, 12, 20, 112),
      children: <Widget>[
        Text(item.bestTitle, style: theme.textTheme.headlineSmall),
        if (item.sourceDomain != null && item.sourceDomain!.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              item.sourceDomain!,
              style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.outline),
            ),
          ),
        if (item.isFailed)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: _Banner(
              text: "FindBack couldn't read this one, so there's no summary yet. "
                  'Save the link again to retry, or open the original.',
              failed: true,
            ),
          )
        else if (!item.isReady)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: _Banner(
              text: item.summary == null || item.summary!.isEmpty
                  ? 'Processing...'
                  : 'Processing...',
            ),
          ),
        if (item.needsRetry)
          const Padding(padding: EdgeInsets.only(top: 8), child: Text('Improving brief...')),
        if (item.missingInfo != null && item.missingInfo!.isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 8), child: _Banner(text: item.missingInfo!)),
        const SizedBox(height: 16),
        Text(
          item.briefText.isEmpty ? 'Open the original to view this memory.' : item.briefText,
          style: theme.textTheme.bodyLarge,
        ),
        if (item.bestTakeaway != null && item.bestTakeaway!.isNotEmpty)
          Padding(padding: const EdgeInsets.only(top: 10), child: Text(item.bestTakeaway!, style: theme.textTheme.titleSmall)),
        if (item.pointsWithRefs.isNotEmpty || item.keyPoints.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 20),
            child: Card(
              margin: EdgeInsets.zero,
              color: theme.colorScheme.primaryContainer,
              clipBehavior: Clip.antiAlias,
              child: ExpansionTile(
                key: PageStorageKey<String>('full-brief-${item.id}'),
                textColor: theme.colorScheme.onPrimaryContainer,
                collapsedTextColor: theme.colorScheme.onPrimaryContainer,
                title: Text('Full Brief', style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700)),
                subtitle: Text('${item.pointsWithRefs.isNotEmpty ? item.pointsWithRefs.length : item.keyPoints.length} key points · Tap to expand'),
                childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                children: <Widget>[
                  if (item.pointsWithRefs.isNotEmpty)
                    for (final BriefKeyPoint point in item.pointsWithRefs)
                      Padding(
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: <Widget>[
                            Text(point.point, style: theme.textTheme.bodyLarge?.copyWith(height: 1.45, color: theme.colorScheme.onPrimaryContainer)),
                            if (point.sourceRef != null)
                              point.timestampUrl(item.url) == null
                                  ? Padding(padding: const EdgeInsets.only(top: 6), child: Text(point.sourceRef!, style: theme.textTheme.bodySmall))
                                  : TextButton(onPressed: () => _openOriginal(point.timestampUrl(item.url)!), child: Text(point.sourceRef!)),
                          ],
                        ),
                      )
                  else
                    for (final String point in item.keyPoints) _Bullet(text: point),
                ],
              ),
            ),
          ),
        if (ingredients.isNotEmpty) ...<Widget>[
          const SizedBox(height: 20),
          Text('Ingredients', style: theme.textTheme.titleMedium),
          const SizedBox(height: 6),
          for (final String ingredient in ingredients) _Bullet(text: ingredient),
        ],
        if (item.tags.isNotEmpty) ...<Widget>[
          const SizedBox(height: 20),
          ExpansionTile(
            key: PageStorageKey<String>('tags-${item.id}'),
            title: const Text('Tags'),
            childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
            children: <Widget>[
              Align(
                alignment: Alignment.centerLeft,
                child: Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: item.tags.map((String tag) => Chip(label: Text(tag))).toList(growable: false),
                ),
              ),
            ],
          ),
        ],
        const SizedBox(height: 28),
        Row(
          children: <Widget>[
            Expanded(
              child: OutlinedButton.icon(
                onPressed: () => _copySummary(item),
                icon: const Icon(Icons.copy_all),
                label: const Text('Copy Summary'),
              ),
            ),
            if (item.category == 'recipe') ...<Widget>[
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton.tonalIcon(
                  onPressed: _toggleCookMode,
                  icon: Icon(_cookMode ? Icons.dark_mode : Icons.restaurant),
                  label: Text(_cookMode ? 'Exit Cook Mode' : 'Cook Mode'),
                ),
              ),
            ],
          ],
        ),
        const SizedBox(height: 12),
        TextButton.icon(
          style: TextButton.styleFrom(foregroundColor: theme.colorScheme.error),
          onPressed: () => _confirmDelete(item),
          icon: const Icon(Icons.delete_outline),
          label: const Text('Delete'),
        ),
      ],
    );
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
