import 'dart:async';
import 'package:flutter/material.dart';

class RefreshHint extends StatefulWidget {
  const RefreshHint({super.key, required this.atTop, required this.onRefresh});

  final bool atTop;
  final VoidCallback onRefresh;

  @override
  State<RefreshHint> createState() => _RefreshHintState();
}

class _RefreshHintState extends State<RefreshHint> {
  Timer? _timer;
  bool _visible = false;

  void _show() {
    _timer?.cancel();
    _visible = true;
    _timer = Timer(const Duration(seconds: 3), () {
      if (mounted) setState(() => _visible = false);
    });
  }

  @override
  void initState() {
    super.initState();
    if (widget.atTop) _show();
  }

  @override
  void didUpdateWidget(RefreshHint oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.atTop && !oldWidget.atTop) _show();
    if (!widget.atTop) {
      _timer?.cancel();
      _visible = false;
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_visible) return const SizedBox.shrink();
    final theme = Theme.of(context);
    return Semantics(
      button: true,
      label: 'Pull down to refresh',
      onTap: widget.onRefresh,
      excludeSemantics: true,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: widget.onRefresh,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
          child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
            TweenAnimationBuilder<double>(
              tween: Tween(begin: -3, end: 0),
              duration: MediaQuery.disableAnimationsOf(context)
                  ? Duration.zero
                  : const Duration(milliseconds: 900),
              builder: (context, offset, child) =>
                  Transform.translate(offset: Offset(0, offset), child: child),
              child: Icon(Icons.arrow_downward,
                  size: 14, color: theme.colorScheme.onSurfaceVariant),
            ),
            const SizedBox(width: 6),
            Flexible(
                child: Text('Pull down to refresh',
                    style: theme.textTheme.labelSmall
                        ?.copyWith(color: theme.colorScheme.onSurfaceVariant))),
          ]),
        ),
      ),
    );
  }
}
