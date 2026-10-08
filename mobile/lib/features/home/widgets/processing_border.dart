import 'package:flutter/material.dart';
import '../../../theme.dart';

/// Repaints only the card outline; its contents do not rebuild every frame.
class ProcessingBorder extends StatefulWidget {
  const ProcessingBorder({super.key, required this.active, required this.shape,
    required this.child});

  final bool active;
  final ShapeBorder shape;
  final Widget child;

  @override
  State<ProcessingBorder> createState() => _ProcessingBorderState();
}

class _ProcessingBorderState extends State<ProcessingBorder>
    with SingleTickerProviderStateMixin {
  late final _light = AnimationController(vsync: this,
      duration: const Duration(seconds: 3));

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _updateMotion();
  }

  @override
  void didUpdateWidget(ProcessingBorder oldWidget) {
    super.didUpdateWidget(oldWidget);
    _updateMotion();
  }

  void _updateMotion() {
    if (widget.active && !MediaQuery.disableAnimationsOf(context) && TickerMode.valuesOf(context).enabled) {
      if (!_light.isAnimating) _light.repeat();
    } else {
      _light.stop();
    }
  }

  @override
  void dispose() {
    _light.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => CustomPaint(
    key: widget.active ? const ValueKey('processing-border') : null,
    foregroundPainter: widget.active ? _BorderLight(_light, widget.shape,
        FindBackTheme.light[FindBackColor.accent]!,
        MediaQuery.disableAnimationsOf(context)) : null,
    child: widget.child,
  );
}

class _BorderLight extends CustomPainter {
  _BorderLight(this.light, this.shape, this.color, this.still) : super(repaint: light);

  final Animation<double> light;
  final ShapeBorder shape;
  final Color color;
  final bool still;

  @override
  void paint(Canvas canvas, Size size) {
    final path = shape.getOuterPath((Offset.zero & size).deflate(1));
    final paint = Paint()..style = PaintingStyle.stroke..strokeWidth = 2;
    for (final edge in path.computeMetrics()) {
      for (double start = 0; start < edge.length; start += 10) {
      canvas.drawPath(edge.extractPath(start, (start + 5).clamp(0, edge.length)), paint..color = color.withValues(alpha: .65));
      }
    }
    if (still) return;
    for (final edge in path.computeMetrics()) {
      // A fading light follows the actual rounded outline, including corners.
      for (var i = 0; i < 16; i++) {
        final start = ((light.value + i * .01) % 1) * edge.length;
        final end = start + edge.length * .011;
        paint.color = color.withValues(alpha: .05 + i * .055);
        canvas.drawPath(edge.extractPath(start, end.clamp(0, edge.length)), paint);
        if (end > edge.length) canvas.drawPath(edge.extractPath(0, end - edge.length), paint);
      }
    }
  }

  @override
  bool shouldRepaint(_BorderLight old) => old.light != light || old.shape != shape ||
      old.color != color || old.still != still;
}
