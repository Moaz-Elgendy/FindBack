import 'package:flutter/material.dart';

class TopBarStatus extends StatefulWidget {
  const TopBarStatus(
      {super.key,
      required this.queued,
      required this.processing,
      required this.onAccount,
      this.signedIn = false});

  final int queued;
  final int processing;
  final VoidCallback onAccount;
  final bool signedIn;

  @override
  State<TopBarStatus> createState() => _TopBarStatusState();
}

class _TopBarStatusState extends State<TopBarStatus> {
  final List<bool> _order = [];

  void _updateOrder() {
    // Simultaneous activations are ordered queue first, then processing.
    for (final queue in [true, false]) {
      final active = (queue ? widget.queued : widget.processing) > 0;
      if (!active) {
        _order.remove(queue);
      } else if (!_order.contains(queue)) {
        _order.add(queue);
      }
    }
  }

  @override
  void initState() {
    super.initState();
    _updateOrder();
  }

  @override
  void didUpdateWidget(TopBarStatus oldWidget) {
    super.didUpdateWidget(oldWidget);
    _updateOrder();
  }

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (final queue in _order.reversed)
            Padding(
              padding: const EdgeInsets.only(right: 6),
              child: Semantics(
                label:
                    '${queue ? 'Queued' : 'Processing'} ${queue ? widget.queued : widget.processing}',
                excludeSemantics: true,
                child: Container(
                  constraints: const BoxConstraints(maxWidth: 64),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.secondaryContainer,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Text(
                      '${queue ? 'Q' : 'P'} ${queue ? widget.queued : widget.processing}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: Theme.of(context)
                              .colorScheme
                              .onSecondaryContainer)),
                ),
              ),
            ),
          SizedBox(
              width: 48,
              height: 48,
              child: IconButton(
                  tooltip: 'Account',
                  onPressed: widget.onAccount,
                  icon: Icon(
                      widget.signedIn ? Icons.person : Icons.person_outline))),
        ],
      );
}
