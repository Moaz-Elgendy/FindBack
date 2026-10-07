import 'dart:async';

import 'package:flutter/foundation.dart';

import '../../data/api_client.dart';
import '../../models/search_result.dart';
import '../../services/sync_service.dart' show ConnectivityProbe, systemIsOnline;

typedef RemoteSearch = Future<SearchResponse> Function(String query, String? category, Map<String, String> filters);
typedef LocalSearch = Future<List<SearchResult>> Function(String query, {String? category, Map<String, String>? filters});

/// Debounced search with an offline path, ported from the RN `useSearch` hook.
///
/// Online answers come from the hybrid API; offline (or when the API errors) we
/// degrade to the SQLite mirror and say so, instead of showing a spinner that
/// never ends.
class SearchController extends ChangeNotifier {
  SearchController({
    required RemoteSearch remote,
    required LocalSearch local,
    required ConnectivityProbe isOnline,
    Duration debounce = const Duration(milliseconds: 250),
  })  : _remote = remote,
        _local = local,
        _isOnline = isOnline,
        _debounce = debounce;

  factory SearchController.of({
    required ApiClient api,
    required LocalSearch local,
    ConnectivityProbe? isOnline,
  }) =>
      SearchController(
        remote: (String query, String? category, Map<String, String> filters) => api.search(query, category: category, filters: filters),
        local: local,
        isOnline: isOnline ?? systemIsOnline,
      );

  final RemoteSearch _remote;
  final LocalSearch _local;
  final ConnectivityProbe _isOnline;
  final Duration _debounce;

  Timer? _debounceTimer;
  String _query = '';
  String _category = 'All';
  Map<String, String> filters = {};
  List<SearchResult> _results = const <SearchResult>[];
  bool _loading = false;
  bool _offline = false;
  int? _tookMs;
  int _requestId = 0;

  String get query => _query;
  String get category => _category;
  List<SearchResult> get results => _results;
  bool get loading => _loading;
  bool get offline => _offline;
  int? get tookMs => _tookMs;
  bool get hasQuery => _query.trim().isNotEmpty;

  /// Text-field entry point: coalesces keystrokes into one request.
  void onQueryChanged(String value) {
    ++_requestId;
    _results = const <SearchResult>[];
    _query = value;
    notifyListeners();
    _debounceTimer?.cancel();
    _debounceTimer = Timer(_debounce, () => run(value, _category));
  }

  void onCategoryChanged(String category) {
    _category = category;
    notifyListeners();
    if (hasQuery) run(_query, category);
  }

  /// Runs immediately (chip taps, retries). Results from an older query are
  /// discarded, so a slow request cannot overwrite fresher keystrokes.
  Future<void> run([String? value, String? category]) async {
    final id = ++_requestId;
    final text = (value ?? _query).trim();
    _query = value ?? _query;
    if (category != null) _category = category;
    if (text.isEmpty) {
      _results = const <SearchResult>[];
      _tookMs = null;
      _loading = false;
      notifyListeners();
      return;
    }

    final selectedCategory = _category;
    final selectedFilters = Map<String, String>.of(filters);
    _results = const <SearchResult>[];
    _loading = true;
    notifyListeners();

    try {
      final bool online = await _isOnline();
      _offline = !online;
      if (_offline) {
        final rows = await _local(text, category: selectedCategory, filters: selectedFilters);
        if (id != _requestId) return;
        _results = rows;
        _tookMs = null;
      } else {
        final response = await _remote(text, selectedCategory, selectedFilters);
        if (id != _requestId) return;
        _results = response.results;
        _tookMs = response.tookMs;
      }
    } on ApiException catch (error) {
      debugPrint('[search] remote failed: $error');
      await _fallback(text, id, selectedCategory, selectedFilters);
    } catch (error) {
      debugPrint('[search] failed: $error');
      await _fallback(text, id, selectedCategory, selectedFilters);
    } finally {
      if (id == _requestId) {
        _loading = false;
        notifyListeners();
      }
    }
  }

  Future<void> _fallback(String text, int id, String category, Map<String, String> filters) async {
    try {
      final rows = await _local(text, category: category, filters: filters);
      if (id != _requestId) return;
      _results = rows
          .map((SearchResult row) => row.copyWith(matchReason: 'Offline fallback', score: 0.3))
          .toList(growable: false);
      _offline = true;
      _tookMs = null;
    } catch (localError) {
      debugPrint('[search] local fallback failed: $localError');
    }
  }

  @override
  void dispose() {
    _debounceTimer?.cancel();
    super.dispose();
  }
}
