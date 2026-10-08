import 'dart:async';
import 'package:flutter/material.dart';
import '../../app_services.dart';
import '../../models/item.dart';
import '../../models/memory_collection.dart';
import '../../models/search_result.dart';
import '../../services/auth_service.dart';
import '../../services/collections_service.dart';
import '../account/account_page.dart';
import '../home/detail_page.dart';
import '../home/widgets/result_card.dart';

class CollectionsPage extends StatefulWidget {
  const CollectionsPage({super.key, required this.services, this.auth});
  final AppServices services;
  final AuthService? auth;
  @override
  State<CollectionsPage> createState() => _CollectionsPageState();
}

class _CollectionsPageState extends State<CollectionsPage> {
  List<MemoryCollection> _collections = [];
  List<ItemDetail> _memories = [];
  String? _error;
  bool _loading = true;
  CollectionsService get service => widget.services.collections;

  @override
  void initState() { super.initState(); _load(); }

  Future<void> _load() async {
    try {
      await _readLocal();
      String? cursor;
      do {
        final page = await widget.services.items.recentPage(limit: 100, cursor: cursor);
        cursor = page.nextCursor;
      } while (cursor != null && mounted);
      if (!mounted) return;
      await widget.services.refreshCollections();
      await _readLocal();
    } catch (_) {
      if (mounted) setState(() { _loading = false; _error = 'Could not sync collections. Your saved collections are still here.'; });
    }
  }

  Future<void> _readLocal() async {
    final collections = await service.list();
    final memories = await service.memories();
    if (mounted) setState(() { _collections = collections; _memories = memories; _loading = false; _error = null; });
  }

  Future<void> _edit({MemoryCollection? collection, bool suggestion = false}) async {
    final name = await collectionName(context, collection?.name ?? '');
    if (name == null || !mounted) return;
    final urls = await chooseMemories(context, _memories, collection?.urls ?? []);
    if (urls == null || !mounted) return;
    await service.save(id: suggestion ? null : collection?.id, name: name, urls: urls);
    await _readLocal();
    unawaited(widget.services.refreshCollections());
  }

  Future<void> _account() async {
    final auth = widget.auth ?? AuthService();
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => AccountPage(auth: auth)));
    if (widget.auth == null) auth.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final savedGroups = _collections.map((c) => c.urls.toSet()).toList();
    final suggestions = collectionSuggestions(_memories).where((s) =>
      !savedGroups.any((g) => g.length == s.urls.length && g.containsAll(s.urls))).take(8).toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Collections'), actions: [
        IconButton(tooltip: 'Account', icon: const Icon(Icons.person_outline), onPressed: _account),
      ]),
      floatingActionButton: FloatingActionButton.extended(onPressed: () => _edit(),
        icon: const Icon(Icons.add), label: const Text('New collection')),
      body: RefreshIndicator(onRefresh: _load, child: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(), slivers: [
          SliverPadding(padding: const EdgeInsets.fromLTRB(20, 8, 20, 20), sliver: SliverToBoxAdapter(child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('A place for things that belong together.', style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
              if (widget.services.guest) Padding(padding: const EdgeInsets.only(top: 8), child: Text('Kept on this device. Sign in to keep them across devices.', style: theme.textTheme.bodySmall)),
              if (!widget.services.guest) Padding(padding: const EdgeInsets.only(top: 8), child: Text('Edits save here and sync when online.', style: theme.textTheme.bodySmall)),
              if (_error != null) TextButton(onPressed: _load, child: Text('$_error Tap to retry.')),
              if (_loading) const LinearProgressIndicator(),
            ],
          ))),
          if (_collections.isEmpty) SliverToBoxAdapter(child: Padding(padding: const EdgeInsets.fromLTRB(20, 8, 20, 28), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Your collections start here', style: theme.textTheme.titleLarge),
              const SizedBox(height: 8), const Text('Gather related memories without moving them out of your library.'),
            ],
          ))),
          _grid(_collections, suggested: false),
          if (suggestions.isNotEmpty) ...[
            SliverToBoxAdapter(child: Padding(padding: const EdgeInsets.fromLTRB(20, 28, 20, 16), child: Column(
              crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Suggested for you', style: theme.textTheme.titleLarge),
                const SizedBox(height: 6), const Text('Related memories. Nothing is grouped until you choose.'),
              ],
            ))),
            _grid(suggestions, suggested: true),
          ],
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      )),
    );
  }

  Widget _grid(List<MemoryCollection> groups, {required bool suggested}) => SliverPadding(
    padding: const EdgeInsets.symmetric(horizontal: 16),
    sliver: SliverLayoutBuilder(builder: (context, constraints) {
      final largeText = MediaQuery.textScalerOf(context).scale(14) > 21;
      return SliverGrid.builder(
        gridDelegate: SliverGridDelegateWithMaxCrossAxisExtent(maxCrossAxisExtent: largeText ? constraints.crossAxisExtent : 320,
          mainAxisExtent: largeText ? 310 : 250, crossAxisSpacing: 12, mainAxisSpacing: 16),
        itemCount: groups.length, itemBuilder: (context, index) {
          final c = groups[index];
          final members = _memories.where((m) => c.urls.contains(m.canonicalUrl)).toList();
          return CollectionCover(collection: c, memories: members, suggested: suggested,
            onTap: () async {
              if (suggested) { await _edit(collection: c, suggestion: true); return; }
              await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => CollectionDetailPage(
                collection: c, services: widget.services)));
              await _readLocal();
            });
        });
    }),
  );
}

class CollectionCover extends StatelessWidget {
  const CollectionCover({super.key, required this.collection, required this.memories, required this.onTap, this.suggested = false});
  final MemoryCollection collection;
  final List<ItemDetail> memories;
  final VoidCallback onTap;
  final bool suggested;
  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final covers = memories.take(4).toList();
    Widget cover(ItemDetail? item) => ColoredBox(color: theme.colorScheme.surfaceContainerHighest,
      child: item?.thumbnailUrl?.isNotEmpty == true ? Image.network(item!.thumbnailUrl!, fit: BoxFit.cover,
        cacheWidth: 400, errorBuilder: (context, error, stackTrace) => _fallback(context, item)) : _fallback(context, item));
    return Card.outlined(margin: EdgeInsets.zero, clipBehavior: Clip.antiAlias, child: InkWell(onTap: onTap,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: Row(children: [
          Expanded(child: covers.length < 2 ? cover(covers.firstOrNull) : Column(children: [
            Expanded(child: cover(covers[0])), const SizedBox(height: 2), Expanded(child: cover(covers[1])),
          ])),
          if (covers.length > 2) ...[const SizedBox(width: 2), Expanded(child: Column(children: [
            Expanded(child: cover(covers[2])), if (covers.length > 3) ...[const SizedBox(height: 2), Expanded(child: cover(covers[3]))],
          ]))],
        ])),
        Padding(padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(collection.name, maxLines: 2, overflow: TextOverflow.ellipsis,
            style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w700, color: theme.colorScheme.primary)),
          const SizedBox(height: 4), Text('${memories.length} ${memories.length == 1 ? 'memory' : 'memories'}${suggested ? ' · Create collection' : ''}',
            maxLines: 2, style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
        ])),
      ])));
  }
  Widget _fallback(BuildContext context, ItemDetail? item) => Center(child: Padding(padding: const EdgeInsets.all(12),
    child: Text(item?.bestTitle ?? collection.name, maxLines: 3, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center,
      style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: Theme.of(context).colorScheme.primary))));
}

Future<String?> collectionName(BuildContext context, String initial) async {
  final controller = TextEditingController(text: initial);
  String? error;
  final result = await showDialog<String>(context: context, builder: (context) => StatefulBuilder(builder: (context, update) => AlertDialog(
    title: const Text('Collection name'),
    content: TextField(controller: controller, autofocus: true, maxLength: 80,
      decoration: InputDecoration(hintText: 'For example, Claude tools', errorText: error)),
    actions: [TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
      FilledButton(onPressed: () {
        if (controller.text.trim().isEmpty) { update(() => error = 'Enter a name'); return; }
        Navigator.pop(context, controller.text.trim());
      }, child: const Text('Continue'))],
  )));
  // The closing route can still paint its text field for one frame.
  WidgetsBinding.instance.addPostFrameCallback((_) => controller.dispose());
  return result;
}

Future<List<String>?> chooseMemories(BuildContext context, List<ItemDetail> memories, List<String> initial) async {
  final selected = initial.toSet().intersection(memories.map((m) => m.canonicalUrl).toSet());
  return showModalBottomSheet<List<String>>(context: context, isScrollControlled: true, useSafeArea: true,
    builder: (context) => StatefulBuilder(builder: (context, update) => SizedBox(
      height: MediaQuery.sizeOf(context).height * .8,
      child: Column(children: [
        Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 8), child: Row(children: [
          Expanded(child: Text('Choose memories', style: Theme.of(context).textTheme.titleLarge)),
          TextButton(onPressed: () => Navigator.pop(context, selected.toList()), child: const Text('Save')),
        ])),
        if (memories.isEmpty) const Padding(padding: EdgeInsets.all(24), child: Text('Save a link first, or keep this collection empty for now.')),
        Expanded(child: ListView.builder(itemCount: memories.length, itemBuilder: (context, index) {
          final item = memories[index];
          return CheckboxListTile(value: selected.contains(item.canonicalUrl), title: Text(item.bestTitle),
            subtitle: Text(item.briefText, maxLines: 2, overflow: TextOverflow.ellipsis),
            onChanged: (value) => update(() { if (value == true) { selected.add(item.canonicalUrl); } else { selected.remove(item.canonicalUrl); } }));
        })),
      ]),
    )));
}

class CollectionDetailPage extends StatefulWidget {
  const CollectionDetailPage({super.key, required this.collection, required this.services});
  final MemoryCollection collection;
  final AppServices services;
  @override
  State<CollectionDetailPage> createState() => _CollectionDetailPageState();
}
class _CollectionDetailPageState extends State<CollectionDetailPage> {
  late MemoryCollection _collection = widget.collection;
  List<ItemDetail> _memories = [];
  @override
  void initState() { super.initState(); _read(); }
  Future<void> _read() async {
    final memories = await widget.services.collections.memories();
    if (mounted) setState(() => _memories = memories);
  }
  Future<void> _edit(String action) async {
    if (action == 'delete') {
      final confirmed = await showDialog<bool>(context: context, builder: (context) => AlertDialog(
        title: const Text('Delete collection?'), content: const Text('Your memories will stay in the library.'),
        actions: [TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          TextButton(onPressed: () => Navigator.pop(context, true), child: const Text('Delete'))]));
      if (confirmed != true) return;
      await widget.services.collections.remove(_collection.id);
      unawaited(widget.services.refreshCollections());
      if (mounted) Navigator.pop(context);
      return;
    }
    String name = _collection.name;
    List<String> urls = _collection.urls;
    if (action == 'rename') {
      final result = await collectionName(context, name);
      if (result == null) return;
      name = result;
    } else {
      final result = await chooseMemories(context, _memories, urls);
      if (result == null) return;
      urls = result;
    }
    final saved = await widget.services.collections.save(id: _collection.id, name: name, urls: urls);
    if (mounted) setState(() => _collection = saved);
    unawaited(widget.services.refreshCollections());
  }
  @override
  Widget build(BuildContext context) {
    final members = _memories.where((m) => _collection.urls.contains(m.canonicalUrl)).toList();
    return Scaffold(appBar: AppBar(title: Text(_collection.name), actions: [
      PopupMenuButton<String>(tooltip: 'Collection options', onSelected: _edit, itemBuilder: (_) => const [
        PopupMenuItem(value: 'edit', child: Text('Edit memories')),
        PopupMenuItem(value: 'rename', child: Text('Rename')),
        PopupMenuItem(value: 'delete', child: Text('Delete collection')),
      ]),
    ]), body: members.isEmpty ? const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('Add memories using Collection options.')))
      : ListView.builder(itemCount: members.length, itemBuilder: (context, index) {
        final item = members[index];
        return ResultCard(result: SearchResult.fromItem(item), onTap: () async {
          await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => DetailPage(itemId: item.id,
            items: widget.services.items, onChanged: _read)));
          await _read();
        });
      }));
  }
}
