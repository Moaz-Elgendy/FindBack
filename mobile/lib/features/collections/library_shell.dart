import 'package:flutter/material.dart';
import '../../app_services.dart';
import '../../services/auth_service.dart';
import '../home/home_screen.dart';
import '../home/capture_sheet.dart';
import 'collections_page.dart';

class LibraryShell extends StatefulWidget {
  const LibraryShell({super.key, required this.services, this.auth});
  final AppServices services;
  final AuthService? auth;
  @override
  State<LibraryShell> createState() => _LibraryShellState();
}
class _LibraryShellState extends State<LibraryShell> {
  int _selected = 0;
  bool _visitedCollections = false;

  Future<void> _save() => CaptureSheet.show(context, capture: widget.services.capture,
      onSaved: (batch) => widget.services.sharedCapture.value = batch);

  Widget _destination(int index, IconData icon, IconData selectedIcon, String label) {
    final selected = _selected == index;
    final colors = Theme.of(context).colorScheme;
    return Expanded(child: Semantics(selected: selected, button: true,
      child: InkWell(onTap: () => setState(() {
        _selected = index;
        if (index == 1) _visitedCollections = true;
      }), child: Padding(padding: const EdgeInsetsDirectional.fromSTEB(4, 10, 4, 10),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(selected ? selectedIcon : icon, color: selected ? colors.primary : colors.onSurfaceVariant),
          const SizedBox(height: 4),
          Text(label, maxLines: 1, overflow: TextOverflow.ellipsis, textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodySmall?.copyWith(
            color: selected ? colors.primary : colors.onSurfaceVariant, fontWeight: FontWeight.w600, fontSize: 13)),
        ])),
      ),
    ));
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: IndexedStack(index: _selected, children: [
      HomeScreen(services: widget.services, auth: widget.auth, embedded: true),
      _visitedCollections ? CollectionsPage(services: widget.services, auth: widget.auth) : const SizedBox.shrink(),
    ]),
    bottomNavigationBar: Material(color: Theme.of(context).scaffoldBackgroundColor,
      child: SafeArea(top: false, child: SizedBox(height: 84 + (MediaQuery.textScalerOf(context).scale(13) - 13).clamp(0, double.infinity) * 5, child: Stack(children: [
        Positioned(top: 16, left: 0, right: 0, bottom: 0, child: DecoratedBox(
          decoration: BoxDecoration(color: Theme.of(context).colorScheme.surface,
            border: Border(top: BorderSide(color: Theme.of(context).colorScheme.outlineVariant))),
          child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _destination(0, Icons.bookmarks_outlined, Icons.bookmarks, 'Library'),
            const SizedBox(width: 92),
            _destination(1, Icons.collections_bookmark_outlined, Icons.collections_bookmark, 'Collections'),
          ]))),
        Align(alignment: Alignment.topCenter, child: DecoratedBox(
          decoration: BoxDecoration(shape: BoxShape.circle, boxShadow: [
            BoxShadow(color: Theme.of(context).scaffoldBackgroundColor, spreadRadius: 6),
            BoxShadow(color: Theme.of(context).colorScheme.primary.withValues(alpha: .3), blurRadius: 22, offset: const Offset(0, 12)),
          ]),
          child: SizedBox(width: 60, height: 60, child: FloatingActionButton(
            elevation: 0, focusElevation: 0, hoverElevation: 0, highlightElevation: 0,
            heroTag: 'library-save', tooltip: 'Save a link', onPressed: _save, shape: const CircleBorder(),
            backgroundColor: Theme.of(context).colorScheme.primary,
            foregroundColor: Theme.of(context).colorScheme.onPrimary,
            child: const Icon(Icons.add, size: 32))))),
      ]))),
    ),
  );
}
