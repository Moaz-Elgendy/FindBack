import 'dart:async';
import 'package:flutter/material.dart';
import '../../app_services.dart';
import '../../models/item.dart';
import '../../models/memory_collection.dart';
import '../../models/search_result.dart';
import '../../services/auth_service.dart';
import '../../services/collections_service.dart';
import '../../widgets/feedback.dart';
import '../../theme.dart';
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

class _CollectionsPageState extends State<CollectionsPage> with WidgetsBindingObserver {
  List<MemoryCollection> _collections = [];
  List<ItemDetail> _memories = [];
  String? _error;
  bool _syncing = false;
  CollectionsService get service => widget.services.collections;

  @override
  void initState() { super.initState(); WidgetsBinding.instance.addObserver(this); _load(); }

  @override void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }
  @override void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }
  Future<void> _load() async {
    if (_syncing) return;
    _syncing = true;
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
      if (mounted) setState(() { _error = 'Could not sync collections. Your saved collections are still here.'; });
    } finally {
      _syncing = false;
    }
  }

  Future<void> _readLocal() async {
    final collections = await service.list();
    final memories = await service.memories();
    if (mounted) setState(() { _collections = collections; _memories = memories; _error = null; });
  }

  Future<void> _account() async {
    final auth = widget.auth ?? AuthService();
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => AccountPage(auth: auth)));
    if (widget.auth == null) auth.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final automatic = automaticCollections(_memories);
    final groups = [...automatic, ..._collections];
    return Scaffold(
      appBar: AppBar(title: const Text('Collections'), actions: [
        IconButton(tooltip: 'Account', icon: const Icon(Icons.person_outline), onPressed: _account),
      ]),
      body: CustomScrollView(
        physics: const AlwaysScrollableScrollPhysics(), slivers: [
          SliverPadding(padding: const EdgeInsets.fromLTRB(20, 8, 20, 20), sliver: SliverToBoxAdapter(child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Grouped for you automatically. Nothing to organize.', style: theme.textTheme.bodyLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
              if (widget.services.guest) Padding(padding: const EdgeInsets.only(top: 8), child: Text('Kept on this device. Sign in to keep them across devices.', style: theme.textTheme.bodySmall)),
              if (_error != null) TextButton(onPressed: _load, child: Text('$_error Tap to retry.')),
            ],
          ))),
          if (groups.isEmpty) const SliverToBoxAdapter(child: Padding(padding: EdgeInsets.fromLTRB(20, 8, 20, 28), child: Column(
            crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('Your collections start here'), SizedBox(height: 8),
              Text('As you save related memories, they appear together here.'),
            ],
          ))),
          SliverPadding(padding: const EdgeInsets.symmetric(horizontal: 20), sliver: SliverList.builder(
            itemCount: groups.length, itemBuilder: (context, index) {
              final collection = groups[index];
              return Padding(padding: const EdgeInsets.only(bottom: 10), child: CollectionCover(
                collection: collection, memories: _memories.where((m) => collection.urls.contains(m.canonicalUrl)).toList(),
                onTap: () async {
                  await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => CollectionDetailPage(
                    collection: collection, services: widget.services)));
                  await _readLocal();
                }));
            })),
          const SliverToBoxAdapter(child: SizedBox(height: 100)),
        ],
      ),
    );
  }

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
    final colors = theme.brightness == Brightness.dark ? FindBackTheme.dark : FindBackTheme.light;
    final layers = memories.length.clamp(1, 3);
    return Material(color: colors[FindBackColor.card],
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20), side: BorderSide(color: colors[FindBackColor.line]!)),
      child: InkWell(borderRadius: BorderRadius.circular(20), onTap: onTap,
        child: Padding(padding: const EdgeInsets.all(16), child: Row(children: [
          SizedBox(width: 30 + (layers - 1) * 20.0, height: 40, child: Stack(children: [
            for (var i = 0; i < layers; i++) PositionedDirectional(start: i * 20.0, child: Container(
              width: 30, height: 40, decoration: BoxDecoration(
                color: colors[[FindBackColor.soft, FindBackColor.stack2, FindBackColor.stack3][i]],
                borderRadius: BorderRadius.circular(8), border: Border.all(color: colors[FindBackColor.card]!, width: 2)))),
          ])),
          const SizedBox(width: 14),
          Expanded(child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(collection.name, maxLines: 3, overflow: TextOverflow.ellipsis, style: theme.textTheme.titleMedium?.copyWith(fontSize: 16.5, fontWeight: FontWeight.w600)),
            const SizedBox(height: 4), Text('${memories.length} ${memories.length == 1 ? 'save' : 'saves'}',
              style: theme.textTheme.bodySmall?.copyWith(fontSize: 13.5, color: theme.colorScheme.onSurfaceVariant)),
          ])),
          const SizedBox(width: 8), Icon(Icons.chevron_right, color: theme.colorScheme.onSurfaceVariant),
        ]))));
  }
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
      if (!_collection.id.startsWith('automatic-')) FindBackActionMenu(tooltip: 'Collection options', actions: [
        FindBackAction(label: 'Edit memories', onPressed: () => _edit('edit')),
        FindBackAction(label: 'Rename', onPressed: () => _edit('rename')),
        FindBackAction(label: 'Delete collection', destructive: true, onPressed: () => _edit('delete')),
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
