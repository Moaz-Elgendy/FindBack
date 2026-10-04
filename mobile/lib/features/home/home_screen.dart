import 'package:flutter/foundation.dart';
// Material 3 exports its own `SearchController` (for `SearchAnchor`), which
// collides with this app's controller; hide the framework one.
import 'package:flutter/material.dart' hide SearchController;

import '../../app_services.dart';
import '../../data/api_client.dart';
import '../../models/search_result.dart';
import '../../services/capture_service.dart';
import 'capture_sheet.dart';
import 'detail_page.dart';
import 'search_controller.dart';
import 'widgets/result_card.dart';

/// The one screen of the MVP: search box, category filter, results (or the
/// offline answer set), recent captures, and the save-a-link sheet.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.services});

  final AppServices services;

  static const List<String> categories = <String>[
    'All',
    'article',
    'recipe',
    'video',
    'product',
    'tool',
    'other',
  ];

  static const int recentLimit = 20;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final TextEditingController _input = TextEditingController();
  late final SearchController _search;

  List<SearchResult> _recent = const <SearchResult>[];
  bool _loadingRecent = true;
  String? _recentError;

  @override
  void initState() {
    super.initState();
    _search = SearchController.of(
      api: widget.services.api,
      local: widget.services.db.localSearch,
    )..addListener(() {
        // The offline banner and the queue badge both live in the app bar.
        if (mounted) setState(() {});
      });
    _loadRecent();
  }

  @override
  void dispose() {
    _search.dispose();
    _input.dispose();
    super.dispose();
  }

  Future<void> _loadRecent() async {
    setState(() => _loadingRecent = true);
    try {
      final List<SearchResult> recent = await widget.services.items.recent(limit: HomeScreen.recentLimit);
      if (!mounted) return;
      setState(() {
        _recent = recent;
        _loadingRecent = false;
        _recentError = null;
      });
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _loadingRecent = false;
        _recentError = error.message;
      });
    }
  }

  Future<void> _refreshAll() async {
    await widget.services.refreshPending();
    await _loadRecent();
    if (_search.hasQuery) await _search.run();
  }

  Future<void> _openDetail(String itemId) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        fullscreenDialog: true,
        builder: (BuildContext context) => DetailPage(
          itemId: itemId,
          items: widget.services.items,
          onChanged: _refreshAll,
        ),
      ),
    );
  }

  Future<void> _saveLink() async {
    await CaptureSheet.show(
      context,
      capture: widget.services.capture,
      onSaved: (CaptureOutcome outcome) {
        WidgetsBinding.instance.addPostFrameCallback((_) async {
          await _refreshAll();
          if (!mounted) return;
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text(
                outcome.isQueued
                    ? 'Saved on this device — it uploads when you reconnect.'
                    : 'Saved. FindBack will summarise it shortly.',
              ),
            ),
          );
        });
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('FindBack'),
        actions: <Widget>[
          _QueueBadge(pending: widget.services.pending),
          IconButton(tooltip: 'Refresh', onPressed: _refreshAll, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: <Widget>[
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
              child: TextField(
                controller: _input,
                textInputAction: TextInputAction.search,
                onChanged: _search.onQueryChanged,
                onSubmitted: (String value) => _search.run(value),
                decoration: InputDecoration(
                  hintText: '“that pasta thing with fennel…”',
                  prefixIcon: _search.loading
                      ? const Padding(
                          padding: EdgeInsets.all(12),
                          child: SizedBox(
                              height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                        )
                      : const Icon(Icons.search),
                  suffixIcon: _search.hasQuery
                      ? IconButton(
                          icon: const Icon(Icons.close),
                          onPressed: () {
                            _input.clear();
                            _search.onQueryChanged('');
                          },
                        )
                      : null,
                  border: OutlineInputBorder(borderRadius: BorderRadius.circular(12)),
                  isDense: true,
                ),
              ),
            ),
            SizedBox(
              height: 48,
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                children: <Widget>[
                  for (final String category in HomeScreen.categories)
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: FilterChip(
                        label: Text(category),
                        selected: _search.category == category,
                        onSelected: (_) => _search.onCategoryChanged(category),
                      ),
                    ),
                ],
              ),
            ),
            if (_search.offline)
              Material(
                color: theme.colorScheme.secondaryContainer,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  child: Row(
                    children: <Widget>[
                      const Icon(Icons.cloud_off, size: 16),
                      const SizedBox(width: 8),
                      Text('Offline — searching this device only', style: theme.textTheme.bodySmall),
                    ],
                  ),
                ),
              ),
            Expanded(child: _search.hasQuery ? _buildResults(theme) : _buildRecent(theme)),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: _saveLink,
        icon: const Icon(Icons.add_link),
        label: const Text('Save a link'),
      ),
    );
  }

  Widget _buildResults(ThemeData theme) {
    if (_search.loading && _search.results.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_search.results.isEmpty) {
      return _EmptyState(
        icon: Icons.travel_explore,
        title: 'Nothing matched “${_search.query}”',
        hint: _search.offline
            ? 'This device has no copy of it. Reconnect to search everything you saved.'
            : 'Try fewer words, or a phrase you remember from the page.',
      );
    }
    return RefreshIndicator(
      onRefresh: _refreshAll,
      child: ListView.builder(
        padding: const EdgeInsets.only(bottom: 96, top: 4),
        itemCount: _search.results.length,
        itemBuilder: (BuildContext context, int index) {
          final SearchResult result = _search.results[index];
          return ResultCard(result: result, onTap: () => _openDetail(result.id));
        },
      ),
    );
  }

  Widget _buildRecent(ThemeData theme) {
    if (_loadingRecent && _recent.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_recentError != null) {
      return _EmptyState(
        icon: Icons.wifi_off,
        title: 'Could not load your library',
        hint: _recentError!,
        action: OutlinedButton(onPressed: _loadRecent, child: const Text('Try again')),
      );
    }
    if (_recent.isEmpty) {
      return const _EmptyState(
        icon: Icons.auto_stories_outlined,
        title: 'Nothing saved yet',
        hint: 'Save a link and FindBack keeps it findable by vague memory.',
      );
    }
    return RefreshIndicator(
      onRefresh: _refreshAll,
      child: ListView.builder(
        padding: const EdgeInsets.only(bottom: 96, top: 4),
        itemCount: _recent.length,
        itemBuilder: (BuildContext context, int index) {
          final SearchResult item = _recent[index];
          return ResultCard(result: item, onTap: () => _openDetail(item.id));
        },
      ),
    );
  }
}

/// Depth of the offline write queue, so a save that has not uploaded is visible
/// instead of silently missing from the server.
class _QueueBadge extends StatelessWidget {
  const _QueueBadge({required this.pending});

  final ValueListenable<int> pending;

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<int>(
      valueListenable: pending,
      builder: (BuildContext context, int count, Widget? child) {
        if (count <= 0) return const SizedBox.shrink();
        return Padding(
          padding: const EdgeInsets.only(right: 4),
          child: Chip(
            avatar: const Icon(Icons.cloud_upload_outlined, size: 16),
            label: Text('$count queued'),
            visualDensity: VisualDensity.compact,
          ),
        );
      },
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.icon, required this.title, required this.hint, this.action});

  final IconData icon;
  final String title;
  final String hint;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(icon, size: 40, color: theme.colorScheme.outline),
            const SizedBox(height: 12),
            Text(title, textAlign: TextAlign.center, style: theme.textTheme.titleMedium),
            const SizedBox(height: 6),
            Text(hint, textAlign: TextAlign.center, style: theme.textTheme.bodyMedium),
            if (action != null) ...<Widget>[const SizedBox(height: 16), action!],
          ],
        ),
      ),
    );
  }
}
