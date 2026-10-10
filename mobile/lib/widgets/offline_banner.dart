import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../cache.dart';
import '../format.dart';
import '../theme.dart';

/// A strip across the top saying the screen is showing what the phone saved earlier.
class OfflineBanner extends StatelessWidget {
  const OfflineBanner({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final from = context.watch<OfflineState>().showingSavedFrom;
    if (from == null) return child;
    return Column(
      children: [
        Material(
          color: Palette.warn,
          child: SafeArea(
            bottom: false,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(
                children: [
                  const Icon(
                    Icons.cloud_off_rounded,
                    size: 18,
                    color: Colors.white,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'No connection. Showing what was saved on ${ukDateTime(from.toIso8601String())}. Pull down to try again.',
                      style: const TextStyle(color: Colors.white, fontSize: 13),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
        Expanded(
          child: MediaQuery.removePadding(
            context: context,
            removeTop: true,
            child: child,
          ),
        ),
      ],
    );
  }
}
