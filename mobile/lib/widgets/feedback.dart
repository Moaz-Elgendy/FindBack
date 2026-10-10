import 'package:flutter/material.dart';
import '../data/local_db.dart';

ScaffoldFeatureController<SnackBar, SnackBarClosedReason> showFindBackToast(
    BuildContext context, String message,
    {SnackBarAction? action, Duration duration = const Duration(seconds: 4)}) {
  final messenger = ScaffoldMessenger.of(context);
  messenger.hideCurrentSnackBar();
  return messenger.showSnackBar(SnackBar(
    behavior: SnackBarBehavior.floating,
    content: Text(message),
    duration: duration,
    action: action,
  ));
}

Future<bool> confirmMemoryDeletion(BuildContext context, LocalDb db) async {
  if (await db.deleteConfirmationSuppressed) return true;
  if (!context.mounted) return false;
  var skip = false;
  final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialog) => StatefulBuilder(
            builder: (dialog, update) => AlertDialog(
                  title: const Text('Delete memory?'),
                  content: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Text(
                        'This memory will be removed from your library.'),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text("Don't show this again"),
                      value: skip,
                      onChanged: (value) => update(() => skip = value ?? false),
                    ),
                  ]),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(dialog, false),
                        child: const Text('Cancel')),
                    FilledButton(
                        onPressed: () => Navigator.pop(dialog, true),
                        child: const Text('Delete')),
                  ],
                )),
      ) ??
      false;
  if (confirmed && skip) await db.setDeleteConfirmationSuppressed(true);
  return confirmed;
}

class FindBackAction {
  const FindBackAction(
      {required this.label,
      required this.onPressed,
      this.icon,
      this.destructive = false});
  final String label;
  final VoidCallback onPressed;
  final IconData? icon;
  final bool destructive;
}

class FindBackActionMenu extends StatefulWidget {
  const FindBackActionMenu(
      {super.key,
      required this.actions,
      this.controller,
      this.tooltip = 'Memory actions'});
  final List<FindBackAction> actions;
  final MenuController? controller;
  final String tooltip;

  @override
  State<FindBackActionMenu> createState() => _FindBackActionMenuState();
}

class _FindBackActionMenuState extends State<FindBackActionMenu> {
  final _controller = MenuController();
  MenuController get controller => widget.controller ?? _controller;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return MenuAnchor(
      controller: controller,
      style: MenuStyle(
        backgroundColor: WidgetStatePropertyAll(colors.surface),
        surfaceTintColor: const WidgetStatePropertyAll(Colors.transparent),
        shape: WidgetStatePropertyAll(RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: BorderSide(color: colors.outlineVariant))),
      ),
      menuChildren: [
        for (final action in widget.actions)
          MenuItemButton(
            onPressed: () {
              controller.close();
              action.onPressed();
            },
            leadingIcon: action.icon == null
                ? null
                : Icon(action.icon,
                    color: action.destructive ? colors.error : null),
            child: Text(action.label,
                style:
                    action.destructive ? TextStyle(color: colors.error) : null),
          ),
      ],
      builder: (context, controller, child) => IconButton(
        tooltip: widget.tooltip,
        icon: const Icon(Icons.more_horiz),
        onPressed: () =>
            controller.isOpen ? controller.close() : controller.open(),
      ),
    );
  }
}
