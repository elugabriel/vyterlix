import 'package:flutter/material.dart';

import '../models_plain.dart';
import '../theme.dart';

Color plainColor(BuildContext context, String tone) {
  switch (tone) {
    case 'good':
      return Palette.ok;
    case 'bad':
      return Palette.bad;
    case 'watch':
      return Palette.warn;
    default:
      return Theme.of(context).colorScheme.outlineVariant;
  }
}

/// A figure in everyday words: a sentence with a coloured bar (green, red or amber), and under it,
/// unless `short`, why it matters.
class PlainBlock extends StatelessWidget {
  const PlainBlock({super.key, required this.plain, this.short = false});

  final PlainFigure plain;
  final bool short;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Container(
      padding: const EdgeInsets.only(left: 10),
      decoration: BoxDecoration(
        border: Border(
          left: BorderSide(color: plainColor(context, plain.tone), width: 3),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            plain.says,
            style: TextStyle(
              fontWeight: FontWeight.w600,
              fontSize: short ? 13 : 15,
              height: 1.35,
            ),
          ),
          if (!short) ...[
            const SizedBox(height: 3),
            Text(plain.why, style: TextStyle(color: muted, fontSize: 13)),
          ],
          if (plain.caution != null) ...[
            const SizedBox(height: 3),
            Text(
              plain.caution!,
              style: const TextStyle(color: Palette.warn, fontSize: 12),
            ),
          ],
        ],
      ),
    );
  }
}
