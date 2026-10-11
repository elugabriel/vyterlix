import 'package:flutter/material.dart';


/// The Vyterlix logo (the tile with the V and the name), at any size.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 34});

  final double size;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(size * 0.22),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFF041040).withValues(alpha: 0.3),
            blurRadius: 12,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: Image.asset(
        'assets/logo.png',
        width: size,
        height: size,
        semanticLabel: 'Vyterlix',
        filterQuality: FilterQuality.medium,
      ),
    );
  }
}

/// A small pill: a severity, a role, a status.
class Tag extends StatelessWidget {
  const Tag(this.text, {super.key, this.color, this.onDark = false});

  final String text;
  final Color? color;
  final bool onDark;

  @override
  Widget build(BuildContext context) {
    final base = onDark
        ? Colors.white
        : (color ?? Theme.of(context).colorScheme.onSurfaceVariant);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: base.withValues(alpha: onDark ? 0.18 : 0.11),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: base.withValues(alpha: onDark ? 0.35 : 0.28)),
      ),
      child: Text(
        text,
        style: TextStyle(
          color: base,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Said when something could not be loaded: what happened, and a way to try again.
class ErrorState extends StatelessWidget {
  const ErrorState({super.key, required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.cloud_off_rounded,
              size: 44,
              color: Theme.of(context).colorScheme.outline,
            ),
            const SizedBox(height: 16),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 20),
            FilledButton(
              onPressed: onRetry,
              style: FilledButton.styleFrom(minimumSize: const Size(160, 48)),
              child: const Text('Try again'),
            ),
          ],
        ),
      ),
    );
  }
}

/// A heading above a group of cards.
class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key});

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 24, bottom: 12),
      child: Text(
        text,
        style: Theme.of(context).textTheme.titleMedium
            ?.copyWith(fontWeight: FontWeight.w700),
      ),
    );
  }
}
