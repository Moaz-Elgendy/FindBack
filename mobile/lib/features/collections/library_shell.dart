import 'package:flutter/material.dart';
import '../../app_services.dart';
import '../../services/auth_service.dart';
import '../home/home_screen.dart';
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
  @override
  Widget build(BuildContext context) => Scaffold(
    body: IndexedStack(index: _selected, children: [
      HomeScreen(services: widget.services, auth: widget.auth),
      _visitedCollections ? CollectionsPage(services: widget.services, auth: widget.auth) : const SizedBox.shrink(),
    ]),
    bottomNavigationBar: NavigationBar(selectedIndex: _selected,
      onDestinationSelected: (index) => setState(() { _selected = index; if (index == 1) _visitedCollections = true; }),
      destinations: const [
        NavigationDestination(icon: Icon(Icons.bookmarks_outlined), selectedIcon: Icon(Icons.bookmarks), label: 'Library'),
        NavigationDestination(icon: Icon(Icons.collections_bookmark_outlined), selectedIcon: Icon(Icons.collections_bookmark), label: 'Collections'),
      ]),
  );
}
