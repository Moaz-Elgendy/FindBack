import 'package:flutter/foundation.dart';
// Material 3 exports its own `SearchController` (for `SearchAnchor`), which
// collides with this app's controller; hide the framework one.
import 'package:flutter/material.dart' hide SearchController;

import '../../app_services.dart';
import '../../data/api_client.dart';
import '../../models/search_result.dart';
import '../../models/categories.dart';
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

  static const int recentLimit = 20;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final TextEditingController _input = TextEditingController();
  late final SearchController _search;

  List<SearchResult> _recent = const <SearchResult>[];
  bool _loadingRecent = true;
  String? _recentError;
  List<String> _categories = const ['All'];
  String _recentCategory = 'All';
  int _recentRequestId = 0;
  late int _pendingCount;
  String? _nextCursor;
  bool _loadingMore = false;
  String? _moreError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _pendingCount = widget.services.pending.value;
    widget.services.pending.addListener(_onQueueChanged);
    _search = SearchController.of(
      api: widget.services.api,
      local: widget.services.db.localSearch,
    )..addListener(() {
        // The offline banner and the queue badge both live in the app bar.
        if (mounted) {
          setState(() {});
          if (!_search.hasQuery && _recentCategory != _search.category) _loadRecent();
        }
      });
    widget.services.sharedCapture.addListener(_onSharedCapture);
    _onSharedCapture();
    _loadCategories();
    _loadRecent();
  }

  void _onQueueChanged() {
    final count = widget.services.pending.value;
    if (count < _pendingCount && mounted) _loadRecent();
    _pendingCount = count;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _retryQueued();
  }

  Future<void> _retryQueued() async {
    await widget.services.sync.flush(force: true);
    if (mounted) await _refreshAll();
  }

  Future<void> _loadCategories() async {
    final categories = await loadCategories();
    if (mounted) setState(() => _categories = ['All', ...categories]);
  }

  @override
  void dispose() {
    widget.services.sharedCapture.removeListener(_onSharedCapture);
    WidgetsBinding.instance.removeObserver(this);
    widget.services.pending.removeListener(_onQueueChanged);
    _search.dispose();
    _input.dispose();
    super.dispose();
  }

  Future<void> _loadRecent() async {
    final requestId = ++_recentRequestId;
    _recentCategory = _search.category;
    setState(() {
      _loadingRecent = true;
      _loadingMore = false;
      _moreError = null;
    });
    try {
      final page = await widget.services.items.recentPage(
        limit: HomeScreen.recentLimit, category: _recentCategory);
      if (!mounted || requestId != _recentRequestId) return;
      setState(() {
        _recent = page.items;
        _nextCursor = page.nextCursor;
        _loadingRecent = false;
        _recentError = null;
      });
    } on ApiException catch (error) {
      if (!mounted || requestId != _recentRequestId) return;
      setState(() {
        _loadingRecent = false;
        _recentError = error.message;
      });
    }
  }

  Future<void> _loadMore() async {
    if (_loadingMore || _loadingRecent || _nextCursor == null) return;
    final requestId = _recentRequestId;
    final cursor = _nextCursor;
    setState(() { _loadingMore = true; _moreError = null; });
    try {
      final page = await widget.services.items.recentPage(
          limit: HomeScreen.recentLimit, category: _recentCategory,
          cursor: cursor, loadedCount: _recent.length);
      if (!mounted || requestId != _recentRequestId) return;
      setState(() {
        final ids = _recent.map((item) => item.id).toSet();
        _recent = [..._recent, ...page.items.where((item) => ids.add(item.id))];
        _nextCursor = page.nextCursor;
        _loadingMore = false;
      });
    } on ApiException catch (error) {
      if (!mounted || requestId != _recentRequestId) return;
      setState(() { _loadingMore = false; _moreError = error.message; });
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

  void _onSharedCapture() {
    final outcome = widget.services.sharedCapture.value;
    if (outcome == null) return;
    widget.services.sharedCapture.value = null;
    _handleSaved(outcome);
  }

  void _handleSaved(CaptureOutcome outcome) {
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      await _refreshAll();
      if (!mounted) return;
      if (outcome.alreadyExists) {
        await _showExisting(outcome.reference);
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
          outcome.isQueued
              ? 'Saved on this device — it uploads when you reconnect.'
              : 'Saved. FindBack will summarise it shortly.')));
    });
    WidgetsBinding.instance.scheduleFrame();
  }

  Future<void> _saveLink() async {
    await CaptureSheet.show(context, capture: widget.services.capture, onSaved: _handleSaved);
  }

  Future<void> _showExisting(String id) async {
    try {
      final item = await widget.services.items.getItem(id);
      if (!mounted) return;
      final open = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Already exists'),
          content: SingleChildScrollView(
            child: Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                border: Border.all(color: Theme.of(context).colorScheme.outline),
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(item?.bestTitle ?? 'This link is already saved.',
                      style: Theme.of(context).textTheme.titleMedium),
                  if (item != null && item.briefText.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    Text(item.briefText),
                  ],
                ],
              ),
            ),
          ),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Close')),
            TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Open memory')),
          ],
        ),
      );
      if (open == true && mounted) await _openDetail(id);
    } on ApiException {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('Already exists')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: const Text('FindBack'),
        actions: <Widget>[
          _QueueBadge(pending: widget.services.pending),
          IconButton(tooltip: 'Refresh', onPressed: _retryQueued, icon: const Icon(Icons.refresh)),
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
                  for (final String category in _categories)
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
            ValueListenableBuilder<int>(
              valueListenable: widget.services.pending,
              builder: (context, count, _) => count == 0 ? const SizedBox.shrink()
                  : ValueListenableBuilder<ApiException?>(
                      valueListenable: widget.services.sync.lastError,
                      builder: (context, error, _) => error == null ? const SizedBox.shrink()
                          : Padding(
                              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                              child: Row(children: [
                                Expanded(child: Text(error.kind == ApiFailureKind.unauthorized
                                    ? 'Sign in to upload your saved links.'
                                    : 'Waiting for the backend. Your links are saved on this device.',
                                    style: theme.textTheme.bodySmall)),
                                TextButton(onPressed: _retryQueued, child: const Text('Retry upload')),
                              ]),
                            ),
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
      onRefresh: _retryQueued,
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
      onRefresh: _retryQueued,
      child: ListView.builder(
        padding: const EdgeInsets.only(bottom: 96, top: 4),
        itemCount: _recent.length + (_nextCursor == null ? 0 : 1),
        itemBuilder: (BuildContext context, int index) {
          if (index == _recent.length) {
            return Padding(
              padding: const EdgeInsets.all(16),
              child: Column(children: [
                if (_moreError != null) Text(_moreError!, textAlign: TextAlign.center),
                if (_loadingMore)
                  const CircularProgressIndicator()
                else
                  OutlinedButton(onPressed: _loadMore,
                      child: Text(_moreError == null ? 'Load more' : 'Try again')),
              ]),
            );
          }
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
