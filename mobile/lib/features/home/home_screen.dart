import '../../widgets/feedback.dart';
import 'dart:async';
// Material 3 exports its own `SearchController` (for `SearchAnchor`), which
// collides with this app's controller; hide the framework one.
import 'package:flutter/material.dart' hide SearchController;

import '../../app_services.dart';
import '../../data/api_client.dart';
import '../../models/search_result.dart';
import 'find_filters.dart';
import 'edit_sheet.dart';
import 'widgets/delete_toast.dart';

import '../../services/capture_service.dart';
import 'capture_sheet.dart';
import 'detail_page.dart';
import 'search_controller.dart';
import 'widgets/result_card.dart';
import 'widgets/status_slots.dart';
import '../../services/auth_service.dart';
import '../account/account_page.dart';

/// The one screen of the MVP: search box, intelligence filters, results (or the
/// offline answer set), recent captures, and the save-a-link sheet.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.services, this.auth, this.findMode = false, this.embedded = false});

  final AppServices services;
  final AuthService? auth;
  final bool findMode, embedded;

  static const int recentLimit = 20;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  final TextEditingController _input = TextEditingController();
  final FocusNode _inputFocus = FocusNode();
  final DateTime _findOpenedAt = DateTime.now();
  final Set<String> _deletedIds = {};
  final Map<String, String> _reportedFailures = {};
  final Set<String> _busyActions = {};
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
  int _processingPollRound = 0, _lastProcessingCount = 0;
  Future<void>? _refreshing;
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
    if (!widget.findMode) {
      widget.services.sharedCapture.addListener(_onSharedCapture);
    _onSharedCapture();
    }
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
      if (!mounted) return;
      _reportReprocessFailures(items);
      setState(() {
          _intelligenceItems = items.where((item) => !_deletedIds.contains(item.id)).toList();
          _topics = topicChoices(items);
        });
      _scheduleProcessingRefresh();
      if (widget.services.pending.value > 0) {
        final queued = await widget.services.db.recentLocalItems(limit: 1000);
        if (!mounted) return;
        final ids = items.map((item) => item.id).toSet();
        items.addAll(queued.where((item) => item.isLocalOnly && ids.add(item.id)));
        setState(() {
          _intelligenceItems = items.where((item) => !_deletedIds.contains(item.id)).toList();
        });
      }
    } catch (error) {
      // Recent/search reports connection errors; retain the previous filter choices.
      if (mounted && error is! ApiException) debugPrint('[library] metadata refresh failed');
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
          Text('More filters', style: Theme.of(context).textTheme.titleLarge),
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
    _inputFocus.dispose();
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
        _recent = page.items.where((item) => !_deletedIds.contains(item.id)).toList();
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

  int get _processingCount => (_intelligenceItems.isEmpty ? _recent : _intelligenceItems)
      .where((item) => !item.isLocalOnly && item.isGeneratingBrief).length;

  int get _queuedCount {
    final local = (_intelligenceItems.isEmpty ? _recent : _intelligenceItems).where((item) => item.isLocalOnly && item.isGeneratingBrief).length;
    final count = widget.services.pending.value;
    return local > count ? local : count;
  }

  void _scheduleProcessingRefresh() {
    _processingRefresh?.cancel();
    final count = _processingCount;
    if (count != _lastProcessingCount) _processingPollRound = 0;
    _lastProcessingCount = count;
    if (mounted && count > 0 &&
        (WidgetsBinding.instance.lifecycleState == null || WidgetsBinding.instance.lifecycleState == AppLifecycleState.resumed)) {
      _processingRefresh = Timer(Duration(seconds: const [5, 10, 20, 40, 60][_processingPollRound]), () {
        if (_processingPollRound < 4) _processingPollRound++;
        unawaited(_refreshAll(background: true));
      });
    }
  }

  Future<void> _refreshAll({bool background = false}) => _refreshing ??=
    _performRefresh(background: background).whenComplete(() => _refreshing = null);

  Future<void> _performRefresh({required bool background}) async {
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
          services: widget.services,
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
              ? batch.outcomes.single.queuedMessage
              : 'Saved. Reading it now.')));
    });
    WidgetsBinding.instance.scheduleFrame();
  }

  Future<void> _saveLink() async {
    await CaptureSheet.show(context, capture: widget.services.capture, onSaved: _handleSaved);
  }

  Future<void> _showExisting(String id) async {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: const Text('Already saved'),
      action: SnackBarAction(label: 'View', onPressed: () => _openDetail(id)),
    ));
  }

  Future<void> _openFind() async {
    await Navigator.of(context).push(MaterialPageRoute<void>(
      fullscreenDialog: true,
      builder: (_) => HomeScreen(services: widget.services, auth: widget.auth,
          findMode: true, embedded: true),
    ));
    if (mounted) await _refreshAll(background: true);
  }

  void _reportReprocessFailures(List<SearchResult> items) {
    for (final item in items) {
      final reason = item.reprocessFailure;
      if (reason == null) { _reportedFailures.remove(item.id); continue; }
      if (_reportedFailures[item.id] == reason) continue;
      _reportedFailures[item.id] = reason;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(reason)));
      });
    }
  }

  Future<void> _edit(SearchResult result) async {
    try {
      final item = await widget.services.items.getItem(result.id);
      if (!mounted || item == null) return;
      await EditSheet.show(context, title: item.bestTitle, brief: item.briefText,
        onSave: (title, brief) async {
          await widget.services.actions.edit(item.id, title: title, summary: brief);
          if (mounted) await _refreshAll(background: true);
        });
    } catch (error) { _actionError(error); }
  }

  Future<void> _delete(SearchResult result) async {
    if (!_busyActions.add(result.id)) return;
    try {
      if (!await confirmMemoryDeletion(context, widget.services.db) || !mounted) return;
      setState(() => _deletedIds.add(result.id));
      ++_recentRequestId;
      final deletion = await widget.services.actions.delete(result.id);
      if (!mounted) return;
      DeleteToast.show(context, localOnly: deletion.localOnly, undo: () async {
        await deletion.undo();
        if (!mounted) return;
        setState(() { _deletedIds.remove(result.id); _deletedIds.remove(deletion.id); });
        await _refreshAll(background: true);
      });
      await _refreshAll(background: true);
    } catch (error) {
      if (mounted) { setState(() => _deletedIds.remove(result.id)); _actionError(error); }
    } finally { _busyActions.remove(result.id); }
  }

  Future<void> _runAction(SearchResult result, Future<Object?> Function() action) async {
    if (!_busyActions.add(result.id)) return;
    try { await action();
      if (mounted) await _refreshAll(background: true); }
    catch (error) { _actionError(error); }
    finally { _busyActions.remove(result.id);
    }
  }

  void _actionError(Object error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
        error is ApiException ? error.message : 'Could not update this memory. Try again.')));
      }
    }

  Widget _memoryCard(SearchResult item, {bool matched = false}) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      ResultCard(key: ValueKey(item.id), result: item, onTap: () => _openDetail(item.id),
        onEdit: () => _edit(item), onDelete: () => _delete(item),
        onRetry: () => _runAction(item, () => widget.services.actions.retry(item.id)),
        onKeepLink: () => _runAction(item, () => widget.services.actions.keepLink(item.id))),
      if (matched && item.matchedTerms.isEmpty && item.matchReason == 'similar meaning')
        Padding(padding: const EdgeInsetsDirectional.fromSTEB(32, 0, 32, 8),
          child: Text('Similar to what you described', style: Theme.of(context).textTheme.bodySmall)),
    ],
  );

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: widget.findMode ? AppBar(
        title: const Text('Find'),
        actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
      ]) : AppBar(title: const FittedBox(fit: BoxFit.scaleDown, alignment: AlignmentDirectional.centerStart, child: Text('FindBack')), actions: [
          ValueListenableBuilder<int>(
            valueListenable: widget.services.pending,
            builder: (context, _, child) => TopBarStatus(
              queued: _queuedCount, processing: _processingCount,
              signedIn: widget.auth?.currentSession != null, onAccount: _openAccount)),
        ]),
      body: SafeArea(
        top: false,
        child: _search.hasQuery ? _buildResults(theme) : _buildRecent(theme)),
      floatingActionButton: !widget.findMode && !widget.embedded && _intelligenceItems.isNotEmpty
          ? FloatingActionButton(onPressed: _saveLink, tooltip: 'Save a link', child: const Icon(Icons.add_link)) : null,
    );
  }

  Widget _feedHeader(ThemeData theme) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      if (widget.findMode) ...[
            Padding(
              padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 12),
              child: Container(
          decoration: BoxDecoration(color: theme.colorScheme.surface,
            border: Border.all(color: theme.colorScheme.outline), borderRadius: BorderRadius.circular(16)),
          padding: const EdgeInsetsDirectional.fromSTEB(14, 6, 14, 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            TextField(
                controller: _input, focusNode: _inputFocus, autofocus: true,
              minLines: 2, maxLines: 5,
                textInputAction: TextInputAction.search,
                onChanged: _search.onQueryChanged,
                onSubmitted: (value) => _search.run(value),
                decoration: InputDecoration(
                  hintText: 'e.g. a video about some AI tool that makes presentations…',
                border: InputBorder.none, enabledBorder: InputBorder.none, focusedBorder: InputBorder.none,
                filled: false,
                  suffixIcon: _search.hasQuery
                      ? IconButton(
                          tooltip: 'Clear search',
                          icon: const Icon(Icons.close),
                          onPressed: () {
                            _input.clear();
                            _search.onQueryChanged('');
                          })
                      : null,
              )),
            Semantics(liveRegion: true, child: Text(_search.loading ? 'Finding memories…' : !_search.hasQuery
                ? 'Newest saves first' : '${_search.results.length} ${_search.results.length == 1 ? 'memory' : 'memories'} found',
              style: theme.textTheme.bodySmall)),
          ]),
              )),
        Padding(padding: const EdgeInsetsDirectional.fromSTEB(16, 0, 16, 8), child: Wrap(
          spacing: 8, runSpacing: 4, children: [
            for (final chip in findFilters(_intelligenceItems, _findOpenedAt))
              FilterChip(label: Text(chip.label), selected: _filters[chip.key] == chip.value,
                onSelected: (selected) => _applyFilters({..._filters}
                  ..remove(chip.key)..addAll(selected ? {chip.key: chip.value} : {}))),
          ],
            )),
        Padding(padding: const EdgeInsetsDirectional.fromSTEB(16, 0, 16, 4),
              child: Align(
          alignment: AlignmentDirectional.centerStart, child: TextButton.icon(onPressed: _showFilters, icon: const Icon(Icons.tune, size: 18),
                    label: Text(_filters.isEmpty ? 'More filters' : 'More filters (${_filters.length})')))),
      ] else
        Padding(padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 12), child: OutlinedButton.icon(
          style: OutlinedButton.styleFrom(backgroundColor: theme.colorScheme.surface,
            alignment: AlignmentDirectional.centerStart, padding: const EdgeInsetsDirectional.fromSTEB(16, 16, 16, 16)),
          onPressed: _openFind, icon: const Icon(Icons.search, size: 20),
          label: const Text('What do you remember?'))),
            ValueListenableBuilder<int>(
              valueListenable: widget.services.pending,
              builder: (context, count, _) => count == 0 ? const SizedBox.shrink()
                  : ValueListenableBuilder<ApiException?>(
                      valueListenable: widget.services.sync.lastError,
                      builder: (context, error, _) => error == null ? const SizedBox.shrink()
                          : Padding(
                              padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 8),
                              child: Wrap(
                crossAxisAlignment: WrapCrossAlignment.center, spacing: 8, children: [
                  Text(error.queuedMessage,
                                    style: theme.textTheme.bodySmall),
                              ])))),
            if (_search.offline)
        Padding(
                  padding: const EdgeInsetsDirectional.fromSTEB(16, 4, 16, 8), child: Text('Offline — searching this device only', style: theme.textTheme.bodySmall)),
          ],
        );

  Widget _buildResults(ThemeData theme) => ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: 24, top: 4),
        itemCount: _search.results.isEmpty ? 2 : _search.results.length + 1,
        itemBuilder: (context, index) {
          if (index == 0) return _feedHeader(theme);
          if (_search.results.isEmpty) {
            if (_search.loading) {
              return const Padding(padding: EdgeInsets.all(32),
                child: Center(child: CircularProgressIndicator()));
            }
            return _EmptyState(icon: Icons.travel_explore, title: 'Nothing matches yet',
              hint: _search.offline
                ? 'This device has no copy of it. Reconnect to search everything you saved.'
                : 'Try one thing you remember: a topic, a place, a name.');
          }
          return _memoryCard(_search.results[index - 1], matched: true);
        },
    );

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
    final feed = <SearchResult>[];
    final ids = <String>{};
    for (final item in [..._recent, if (!widget.findMode) ..._intelligenceItems.where((item) => item.isGeneratingBrief || item.isFailed)]) {
      if (!_deletedIds.contains(item.id) && ids.add(item.id)) {
        feed.add(item);
      }
    }
    if (feed.isEmpty && _filters.isEmpty) {
      return _refreshableEmpty(_FirstSave(onSave: _saveLink));
    }
    if (feed.isEmpty) {
      return _refreshableEmpty(const _EmptyState(
        icon: Icons.travel_explore,
        title: 'Nothing matches yet',
        hint: 'Try one thing you remember: a topic, a place, a name.'));
    }
    final order = {for (var i = 0; i < feed.length; i++) feed[i].id: i};
    if (!widget.findMode) {
      feed.sort((a, b) {
      int priority(SearchResult item) => item.isGeneratingBrief ? 0 : item.isFailed ? 1 : 2;
      final state = priority(a).compareTo(priority(b));
      if (state != 0) return state;
      final saved = (b.createdAt ?? DateTime(1970)).compareTo(a.createdAt ?? DateTime(1970));
      return saved != 0 ? saved : order[a.id]!.compareTo(order[b.id]!);
      });
    }
    return ListView.builder(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.only(bottom: 24, top: 4),
        itemCount: feed.length + 1 + (_nextCursor == null ? 0 : 1),
        itemBuilder: (BuildContext context, int index) {
          if (index == 0) return _feedHeader(theme);
          if (index == feed.length + 1) {
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
          final SearchResult item = feed[index - 1];
          return _memoryCard(item);
        },
    );
  }

  Widget _refreshableEmpty(Widget child) => LayoutBuilder(
    builder: (context, bounds) => ListView(physics: const AlwaysScrollableScrollPhysics(), children: [
        _feedHeader(Theme.of(context)),
        ConstrainedBox(constraints: BoxConstraints(minHeight: (bounds.maxHeight - (widget.findMode ? 350 : 100)).clamp(0, double.infinity)), child: child),
      ]),
  );

  Future<void> _openAccount() async {
    final auth = widget.auth ?? AuthService();
    await Navigator.of(context).push(MaterialPageRoute<void>(
        builder: (_) => AccountPage(auth: auth)));
    if (widget.auth == null) await auth.dispose();
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
        padding: const EdgeInsetsDirectional.fromSTEB(24, 24, 24, 24),
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

class _FirstSave extends StatelessWidget {
  const _FirstSave({required this.onSave});
  final VoidCallback onSave;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsetsDirectional.fromSTEB(24, 32, 24, 32),
    child: Column(mainAxisSize: MainAxisSize.min, children: [
      Text('Save your first link', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineSmall),
      const SizedBox(height: 12),
      const Text('Share any link to FindBack from another app, or paste one here.', textAlign: TextAlign.center),
      const SizedBox(height: 20),
      FilledButton(onPressed: onSave, child: const Text('Save a link')),
      const SizedBox(height: 28),
      Row(children: [
        for (final (icon, label) in [(Icons.ios_share, 'Share'), (Icons.auto_stories_outlined, 'Read'), (Icons.search, 'Find')])
          Expanded(child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, color: Theme.of(context).colorScheme.primary), const SizedBox(height: 8),
            Text(label, textAlign: TextAlign.center),
          ])),
      ]),
    ]),
  );
}
