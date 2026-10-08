import 'package:flutter/material.dart';

import '../../../theme.dart';

class TopBarStatus extends StatelessWidget {
  const TopBarStatus(
      {super.key,
      required this.queued,
      required this.processing,
      required this.onAccount,
      this.signedIn = false});

  final int queued, processing;
  final VoidCallback onAccount;
  final bool signedIn;

  @override
  Widget build(BuildContext context) {
    final count = queued + processing;
    final colors = Theme.of(context).brightness == Brightness.dark
        ? FindBackTheme.dark
        : FindBackTheme.light;
    return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
      if (count > 0)
        Flexible(
            child: Padding(
              padding: const EdgeInsetsDirectional.only(end: 6),
              child: Semantics(
                label: 'Reading $count',
                excludeSemantics: true,
                child: Container(
                  constraints: const BoxConstraints(maxWidth: 120),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: colors[FindBackColor.accentSoft],
                    borderRadius: BorderRadius.circular(99)),
                        child: FittedBox(
                            fit: BoxFit.scaleDown,
                  child: Text('Reading $count',
                      maxLines: 1,
                      style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: colors[
                                            FindBackColor.accentInk]))))))),
          SizedBox(
              width: 48,
              height: 48,
              child: IconButton(
                  tooltip: 'Account',
                  onPressed: onAccount,
                  icon: Icon(signedIn ? Icons.person : Icons.person_outline))),
        ]);
}
}
