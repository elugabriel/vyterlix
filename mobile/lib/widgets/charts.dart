import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// Where a value sits between the bottom (0.0) and the top (1.0) of a chart that runs from
/// [low] to [high]. A chart with no spread (everything the same) draws in the middle.
double fractionBetween(double value, double low, double high) {
  if (high == low) return 0.5;
  return ((value - low) / (high - low)).clamp(0.0, 1.0);
}

/// A bar for each period, finished ones solid and the one in progress faded.
class BarChart extends StatelessWidget {
  const BarChart({
    super.key,
    required this.values,
    this.lastIsPartial = false,
    this.height = 140,
  });

  /// Oldest first; null where there is no figure.
  final List<double?> values;
  final bool lastIsPartial;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _BarPainter(
          values,
          lastIsPartial,
          Theme.of(context).colorScheme.primary,
          Theme.of(context).colorScheme.outlineVariant,
        ),
      ),
    );
  }
}

class _BarPainter extends CustomPainter {
  _BarPainter(this.values, this.lastIsPartial, this.color, this.axis);

  final List<double?> values;
  final bool lastIsPartial;
  final Color color;
  final Color axis;

  @override
  void paint(Canvas canvas, Size size) {
    final shown = values.whereType<double>().toList();
    if (shown.isEmpty) return;
    final high = math.max(0.0, shown.reduce(math.max));
    final low = math.min(0.0, shown.reduce(math.min));
    final zero = size.height * (1 - fractionBetween(0, low, high));
    final slot = size.width / values.length;
    final bar = slot * 0.62;
    for (var i = 0; i < values.length; i++) {
      final value = values[i];
      if (value == null) continue;
      final top = size.height * (1 - fractionBetween(value, low, high));
      final rect = Rect.fromLTRB(
        i * slot + (slot - bar) / 2,
        math.min(top, zero),
        i * slot + (slot + bar) / 2,
        math.max(top, zero) + (top == zero ? 1.5 : 0),
      );
      final partial = lastIsPartial && i == values.length - 1;
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(3)),
        Paint()..color = color.withValues(alpha: partial ? 0.4 : 1),
      );
    }
    canvas.drawLine(
      Offset(0, zero),
      Offset(size.width, zero),
      Paint()
        ..color = axis
        ..strokeWidth = 1,
    );
  }

  @override
  bool shouldRepaint(_BarPainter old) =>
      old.values != values || old.lastIsPartial != lastIsPartial;
}

/// What happened (a line), and what is expected next (a dashed line with a band around it).
class LineChart extends StatelessWidget {
  const LineChart({
    super.key,
    required this.history,
    this.expected = const [],
    this.lower = const [],
    this.upper = const [],
    this.actual = const [],
    this.height = 180,
  });

  final List<double> history;
  final List<double> expected;
  final List<double> lower;
  final List<double> upper;

  /// What really happened in a forecast month that has finished (null where not yet).
  final List<double?> actual;
  final double height;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(
        painter: _LinePainter(
          this,
          Theme.of(context).colorScheme.primary,
          Theme.of(context).colorScheme.onSurface,
        ),
      ),
    );
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter(this.chart, this.color, this.ink);

  final LineChart chart;
  final Color color;
  final Color ink;

  @override
  void paint(Canvas canvas, Size size) {
    final all = [
      ...chart.history,
      ...chart.expected,
      ...chart.lower,
      ...chart.upper,
      ...chart.actual.whereType<double>(),
    ];
    if (all.isEmpty) return;
    final high = all.reduce(math.max);
    final low = all.reduce(math.min);
    final points = chart.history.length + chart.expected.length;
    if (points < 2) return;
    final step = size.width / (points - 1);
    Offset at(int index, double value) => Offset(
      index * step,
      size.height * 0.06 +
          (size.height * 0.88) * (1 - fractionBetween(value, low, high)),
    );

    final start = chart.history.length - 1;
    if (chart.upper.isNotEmpty && chart.lower.length == chart.upper.length) {
      final band = Path()
        ..moveTo(
          at(start + 1, chart.upper.first).dx,
          at(start + 1, chart.upper.first).dy,
        );
      for (var i = 0; i < chart.upper.length; i++) {
        final p = at(start + 1 + i, chart.upper[i]);
        band.lineTo(p.dx, p.dy);
      }
      for (var i = chart.lower.length - 1; i >= 0; i--) {
        final p = at(start + 1 + i, chart.lower[i]);
        band.lineTo(p.dx, p.dy);
      }
      band.close();
      canvas.drawPath(band, Paint()..color = color.withValues(alpha: 0.15));
    }

    final line = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round;
    final past = Path();
    for (var i = 0; i < chart.history.length; i++) {
      final p = at(i, chart.history[i]);
      i == 0 ? past.moveTo(p.dx, p.dy) : past.lineTo(p.dx, p.dy);
    }
    canvas.drawPath(past, line);

    if (chart.expected.isNotEmpty) {
      final from = chart.history.isEmpty ? null : at(start, chart.history.last);
      final path = Path();
      if (from != null) path.moveTo(from.dx, from.dy);
      for (var i = 0; i < chart.expected.length; i++) {
        final p = at(start + 1 + i, chart.expected[i]);
        if (from == null && i == 0) {
          path.moveTo(p.dx, p.dy);
        } else {
          path.lineTo(p.dx, p.dy);
        }
      }
      for (final metric in path.computeMetrics()) {
        var distance = 0.0;
        while (distance < metric.length) {
          canvas.drawPath(
            metric.extractPath(distance, math.min(distance + 7, metric.length)),
            line,
          );
          distance += 12;
        }
      }
      for (var i = 0; i < chart.expected.length; i++) {
        canvas.drawCircle(
          at(start + 1 + i, chart.expected[i]),
          3.5,
          Paint()..color = color,
        );
      }
    }
    for (var i = 0; i < chart.actual.length; i++) {
      final value = chart.actual[i];
      if (value == null) continue;
      canvas.drawCircle(
        at(start + 1 + i, value),
        5.5,
        Paint()
          ..color = ink
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }
  }

  @override
  bool shouldRepaint(_LinePainter old) => old.chart != chart;
}

/// The business health score month by month, as a small line.
class Sparkline extends StatelessWidget {
  const Sparkline({super.key, required this.values, this.height = 70});

  final List<double> values;
  final double height;

  @override
  Widget build(BuildContext context) {
    return LineChart(history: values, height: height);
  }
}

/// The colour for a good, a middling and a bad result.
Color goodFairPoor(double score) {
  if (score >= 70) return Palette.ok;
  if (score >= 45) return Palette.warn;
  return Palette.bad;
}
