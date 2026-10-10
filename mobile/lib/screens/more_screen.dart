import 'package:flutter/material.dart';

import '../models.dart';
import 'changes_screen.dart';
import 'forecast_screen.dart';
import 'health_screen.dart';
import 'key_figures_screen.dart';

/// Everything else in the business: the figures and what they mean.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key, required this.business});

  final Business business;

  @override
  Widget build(BuildContext context) {
    final entries = <(IconData, String, String, WidgetBuilder)>[
      (
        Icons.bar_chart_rounded,
        'Key figures',
        'Every figure about your business, month by month.',
        (_) => KeyFiguresScreen(business: business),
      ),
      (
        Icons.favorite_border_rounded,
        'Business health',
        'One score for how you are doing, and why.',
        (_) => HealthScreen(business: business),
      ),
      (
        Icons.trending_up_rounded,
        'Forecast',
        'What to expect over the next months, and what to stock.',
        (_) => ForecastScreen(business: business),
      ),
      (
        Icons.show_chart_rounded,
        'What changed',
        'The figures that moved by more than normal, and why.',
        (_) => ChangesScreen(business: business),
      ),
    ];
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        for (final (icon, title, subtitle, builder) in entries) ...[
          Card(
            child: InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: () =>
                  Navigator.of(context)
                      .push(MaterialPageRoute<void>(builder: builder)),
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: Theme.of(context).colorScheme.primary
                            .withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(12),
                      ),
                      child: Icon(
                        icon,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            title,
                            style: const TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 16,
                            ),
                          ),
                          const SizedBox(height: 2),
                          Text(
                            subtitle,
                            style: TextStyle(color: muted, fontSize: 13),
                          ),
                        ],
                      ),
                    ),
                    const Icon(Icons.chevron_right_rounded),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(height: 12),
        ],
      ],
    );
  }
}
