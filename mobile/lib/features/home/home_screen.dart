import 'dart:async';
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
import 'widgets/status_slots.dart';
import 'widgets/refresh_hint.dart';
import '../../services/auth_service.dart';
import '../account/account_page.dart';

/// The one screen of the MVP: search box, intelligence filters, results (or the
/// offline answer set), recent captures, and the save-a-link sheet.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.services, this.auth});

  final AppServices services;
  final AuthService? auth;

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
  List<String> _topics = const [];
  List<SearchResult> _intelligenceItems = const [];
  Map<String, String> _filters = {};
  String _recentCategory = 'All';
  int _recentRequestId = 0;
  late int _pendingCount;
  String? _nextCursor;
  bool _loadingMore = false;
  bool _wasSearching = false;
  Timer? _processingRefresh;
  bool _atTop = true;
  String? _moreError;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _recent = widget.services.initialLibrary;
    _pendingCount = widget.services.pending.value;
    widget.services.pending.addListener(_onQueueChanged);
    _search = (widget.services.guest ? SearchController(
      remote: (query, category, filters) async => SearchResponse(
        results: await widget.services.db.localSearch(query, category: category, filters: filters), tookMs: 0),
      local: widget.services.db.localSearch, isOnline: () async => true,
    ) : SearchController.of(api: widget.services.api, local: widget.services.db.localSearch))
      ..addListener(() {
        // The offline banner and the queue badge both live in the app bar.
        if (mounted) {
          setState(() {});
          if (!_search.hasQuery && (_wasSearching || _recentCategory != _search.category)) _loadRecent();
          _wasSearching = _search.hasQuery;
        }
      });
    widget.services.sharedCapture.addListener(_onSharedCapture);
    _onSharedCapture();
    _loadIntelligence();
    _loadRecent(background: _recent.isNotEmpty);
    if (widget.services.guest) {
      unawaited(widget.services.refreshGuest().then((_) {
        if (mounted) { _loadIntelligence(); _loadRecent(background: true); }
      }).catchError((Object _) {}));
    }
  }

  void _onQueueChanged() {
    final count = widget.services.pending.value;
    if (count < _pendingCount && mounted) {
      _loadIntelligence();
      _loadRecent();
    }
    _pendingCount = count;
    _scheduleProcessingRefresh();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _retryQueued();
    } else {
      _processingRefresh?.cancel();
    }
  }

  Future<void> _retryQueued() async {
    await widget.services.sync.flush(force: true);
    if (mounted) await _refreshAll();
  }

  Future<void> _loadIntelligence() async {
    // ponytail: scan saved metadata for choices; use server facets if libraries grow large.
    try {
      final items = <SearchResult>[];
      String? cursor;
      do {
        final page = await widget.services.items.recentPage(limit: 100, cursor: cursor, loadedCount: items.length);
        if (!mounted) return;
        items.addAll(page.items);
        cursor = page.nextCursor;
        if (page.items.isEmpty) break;
      } while (cursor != null);
      if (mounted) {
        setState(() {
          _intelligenceItems = items;
          _topics = topicChoices(items);
        });
      }
    } on ApiException {
      // Recent/search reports connection errors; retain the previous filter choices.
    } finally {
      _scheduleProcessingRefresh();
    }
  }

  void _applyFilters(Map<String, String> filters) {
    setState(() => _filters = filters);
    _search.filters = Map.of(filters);
    if (_search.hasQuery) {
      _search.run();
    } else {
      _loadRecent();
    }
  }

  Future<void> _showFilters() async {
    await _loadIntelligence();
    if (!mounted) return;
    final selected = Map<String, String>.of(_filters);
    const labels = {'topic': 'Topic', 'type': 'Content type', 'entity': 'Entity',
      'intent': 'Intent', 'action': 'Action', 'source': 'Source', 'saved': 'Saved date'};
    final result = await showModalBottomSheet<Map<String, String>>(
      context: context, isScrollControlled: true,
      builder: (context) => StatefulBuilder(builder: (context, update) => FractionallySizedBox(
        heightFactor: .85,
        child: SafeArea(child: ListView(padding: const EdgeInsets.all(20), children: [
          Text('Content intelligence', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 16),
          for (final entry in labels.entries) ...[
            DropdownButtonFormField<String>(
              initialValue: selected[entry.key], isExpanded: true,
              decoration: InputDecoration(labelText: entry.value),
              items: [
                const DropdownMenuItem<String>(value: null, child: Text('Any')),
                for (final value in ({..._intelligenceItems.expand((item) => entry.key == 'topic' ? <String>[] : item.intelligence[entry.key] ?? []),
                    if (entry.key == 'topic') ..._topics,
                    if (selected[entry.key] != null) selected[entry.key]!}.toList()..sort()))
                  DropdownMenuItem(value: value, child: Text(value.replaceAll('_', ' '), maxLines: 1, overflow: TextOverflow.ellipsis)),
              ],
              onChanged: (value) => update(() {
                if (value == null) { selected.remove(entry.key); } else { selected[entry.key] = value; }
              }),
            ),
            const SizedBox(height: 12),
          ],
          FilledButton(onPressed: () => Navigator.pop(context, selected), child: const Text('Apply filters')),
          TextButton(onPressed: () => Navigator.pop(context, <String, String>{}), child: const Text('Clear filters')),
        ])),
      )),
    );
    if (mounted && result != null) _applyFilters(result);
  }

  @override
  void dispose() {
    _processingRefresh?.cancel();
    widget.services.sharedCapture.removeListener(_onSharedCapture);
    WidgetsBinding.instance.removeObserver(this);
    widget.services.pending.removeListener(_onQueueChanged);
    _search.dispose();
    _input.dispose();
    super.dispose();
  }

  Future<void> _loadRecent({bool background = false}) async {
    final requestId = ++_recentRequestId;
    _recentCategory = _search.category;
    setState(() {
      if (!background) _recent = [];
      _loadingRecent = true;
      _loadingMore = false;
      _moreError = null;
    });
    try {
      final page = await widget.services.items.recentPage(
        limit: HomeScreen.recentLimit, category: _recentCategory, filters: _filters);
      if (!mounted || requestId != _recentRequestId) return;
      setState(() {
        _recent = page.items;
        _topics = topicChoices([..._intelligenceItems, ...page.items]);
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
          cursor: cursor, loadedCount: _recent.length, filters: _filters);
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

  int get _processingCount => _intelligenceItems
      .where((item) => !item.isLocalOnly && item.isGeneratingBrief).length;

  int get _queuedCount {
    final local = _intelligenceItems.where((item) => item.isLocalOnly && item.isGeneratingBrief).length;
    final count = widget.services.pending.value;
    return local > count ? local : count;
  }

  void _scheduleProcessingRefresh() {
    _processingRefresh?.cancel();
    if (mounted && (_processingCount + _queuedCount) > 0 &&
        (WidgetsBinding.instance.lifecycleState == null ||
         WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed)) {
      _processingRefresh = Timer(const Duration(seconds: 5),
          () => _refreshAll(background: true));
    }
  }

  Future<void> _refreshAll({bool background = false}) async {
    await widget.services.refreshPending();
    await widget.services.refreshGuest();
    await _loadIntelligence();
    if (!mounted) return;
    await _loadRecent(background: background);
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

  void _handleSaved(CaptureBatch batch) {
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      await _refreshAll();
      if (!mounted) return;
      if (batch.outcomes.length == 1 && batch.failedUrls.isEmpty && batch.outcomes.single.alreadyExists) {
        final outcome = batch.outcomes.single;
        await _showExisting(outcome.reference);
        return;
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
          batch.outcomes.length != 1 || batch.failedUrls.isNotEmpty
              ? '${batch.outcomes.length} saved${batch.failedUrls.isEmpty ? "" : "; ${batch.failedUrls.length} could not be saved"}.'
              : batch.isQueued
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
          ValueListenableBuilder<int>(
            valueListenable: widget.services.pending,
            builder: (context, _, child) => TopBarStatus(
              queued: _queuedCount, processing: _processingCount,
              signedIn: widget.auth?.currentSession != null, onAccount: _openAccount),
          ),
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
                          tooltip: 'Clear search',
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
              height: 64 + (MediaQuery.textScalerOf(context).scale(14) - 14),
              child: ListView(
                scrollDirection: Axis.horizontal,
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                children: <Widget>[
                  Padding(padding: const EdgeInsets.only(right: 8), child: ActionChip(
                    avatar: const Icon(Icons.tune, size: 18),
                    label: Text(_filters.isEmpty ? 'Filters' : 'Filters (${_filters.length})'), onPressed: _showFilters)),
                  for (final String topic in ['All', ..._topics])
                    Padding(padding: const EdgeInsets.only(right: 8), child: FilterChip(
                      label: Text(topic), selected: topic == 'All' ? !_filters.containsKey('topic') : _filters['topic'] == topic,
                      onSelected: (_) => _applyFilters({..._filters}..remove('topic')..addAll(topic == 'All' ? {} : {'topic': topic})),
                    )),
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
                      Expanded(child: Text('Offline — searching this device only', style: theme.textTheme.bodySmall)),
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
      return _refreshableEmpty(_EmptyState(
        icon: Icons.travel_explore,
        title: 'Nothing matched “${_search.query}”',
        hint: _search.offline
            ? 'This device has no copy of it. Reconnect to search everything you saved.'
            : 'Try fewer words, or a phrase you remember from the page.',
      ));
    }
    return NotificationListener<ScrollNotification>(
      onNotification: _trackTop, child: RefreshIndicator(
      onRefresh: _retryQueued,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: 96, top: 4),
        itemCount: _search.results.length + 1,
        itemBuilder: (BuildContext context, int index) {
          if (index == 0) return _refreshCue(theme);
          final SearchResult result = _search.results[index - 1];
          return ResultCard(result: result, onTap: () => _openDetail(result.id));
        },
      ),
    ));
  }

  Widget _buildRecent(ThemeData theme) {
    if (_loadingRecent && _recent.isEmpty) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_recentError != null) {
      return _refreshableEmpty(_EmptyState(
        icon: Icons.wifi_off,
        title: 'Could not load your library',
        hint: _recentError!,
        action: OutlinedButton(onPressed: _loadRecent, child: const Text('Try again')),
      ));
    }
    if (_recent.isEmpty) {
      return _refreshableEmpty(const _EmptyState(
        icon: Icons.auto_stories_outlined,
        title: 'Nothing saved yet',
        hint: 'Save a link and FindBack keeps it findable by vague memory.',
      ));
    }
    return NotificationListener<ScrollNotification>(
      onNotification: _trackTop, child: RefreshIndicator(
      onRefresh: _retryQueued,
      child: ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: 96, top: 4),
        itemCount: _recent.length + 1 + (_nextCursor == null ? 0 : 1),
        itemBuilder: (BuildContext context, int index) {
          if (index == 0) return _refreshCue(theme);
          if (index == _recent.length + 1) {
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
          final SearchResult item = _recent[index - 1];
          return ResultCard(result: item, onTap: () => _openDetail(item.id));
        },
      ),
    ));
  }

  Widget _refreshableEmpty(Widget child) => LayoutBuilder(
    builder: (context, bounds) => NotificationListener<ScrollNotification>(
      onNotification: _trackTop, child: RefreshIndicator(
      onRefresh: _retryQueued,
      child: ListView(physics: const AlwaysScrollableScrollPhysics(), children: [
        _refreshCue(Theme.of(context)),
        SizedBox(height: (bounds.maxHeight - 64).clamp(0, double.infinity), child: child),
      ]),
    )),
  );

  Future<void> _openAccount() async {
    final auth = widget.auth ?? AuthService();
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => AccountPage(auth: auth)));
    if (widget.auth == null) await auth.dispose();
  }

  bool _trackTop(ScrollNotification notification) {
    if (notification.depth == 0 && notification.metrics.axis == Axis.vertical) {
      final atTop = notification.metrics.pixels <= notification.metrics.minScrollExtent + .5;
      if (mounted && atTop != _atTop) setState(() => _atTop = atTop);
    }
    return false;
  }

  Widget _refreshCue(ThemeData theme) => RefreshHint(atTop: _atTop, onRefresh: _retryQueued);

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
