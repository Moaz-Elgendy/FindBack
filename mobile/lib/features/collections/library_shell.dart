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
          Text(label, textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodySmall?.copyWith(
            color: selected ? colors.primary : colors.onSurfaceVariant, fontWeight: FontWeight.w600)),
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
    bottomNavigationBar: Material(color: Theme.of(context).colorScheme.surface,
      child: SafeArea(top: false, child: Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
        _destination(0, Icons.bookmarks_outlined, Icons.bookmarks, 'Library'),
        Expanded(child: Center(heightFactor: 1, child: Padding(padding: const EdgeInsets.symmetric(vertical: 8),
          child: SizedBox(width: 56, height: 56, child: FloatingActionButton(
            elevation: 0, focusElevation: 0, hoverElevation: 0, highlightElevation: 0,
            heroTag: 'library-save', tooltip: 'Save a link', onPressed: _save,
            shape: const CircleBorder(), backgroundColor: Theme.of(context).colorScheme.primary,
            foregroundColor: Theme.of(context).colorScheme.onPrimary, child: const Icon(Icons.add_link))),
        ))),
        _destination(1, Icons.collections_bookmark_outlined, Icons.collections_bookmark, 'Collections'),
      ])),
    ),
  );
}
