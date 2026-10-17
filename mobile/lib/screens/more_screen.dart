import 'package:flutter/material.dart';

import '../models.dart';
import '../widgets/common.dart';
import 'ask_screen.dart';
import 'changes_screen.dart';
import 'data_screen.dart';
import 'billing_screen.dart';
import 'forecast_screen.dart';
import 'health_screen.dart';
import 'key_figures_screen.dart';
import 'memory_screen.dart';
import 'reports_screen.dart';
import 'settings_screen.dart';

/// Everything else in the business, grouped by what it is for: understand, explain, predict, follow up.
class MoreScreen extends StatelessWidget {
  const MoreScreen({super.key, required this.business});

  final Business business;

  @override
  Widget build(BuildContext context) {
    final entries = <(String, IconData, String, String, WidgetBuilder)>[
      (
        'Understand',
        Icons.bar_chart_rounded,
        'Key figures',
        'Every figure about your business, month by month.',
        (_) => KeyFiguresScreen(business: business),
      ),
      (
        'Understand',
        Icons.favorite_border_rounded,
        'Business health',
        'One score for how you are doing, and why.',
        (_) => HealthScreen(business: business),
      ),
      (
        'Understand',
        Icons.description_outlined,
        'Reports',
        'Your figures written up to read, kept exactly as written.',
        (_) => ReportsScreen(business: business),
      ),
      (
        'Explain',
        Icons.show_chart_rounded,
        'What changed',
        'The figures that moved by more than normal, and why.',
        (_) => ChangesScreen(business: business),
      ),
      (
        'Explain',
        Icons.auto_awesome_outlined,
        'Ask Vyterlix',
        'Ask a question about your business and get an answer from your own results.',
        (_) => AskScreen(business: business),
      ),
      (
        'Predict',
        Icons.trending_up_rounded,
        'Forecast',
        'What to expect over the next months, and what to stock.',
        (_) => ForecastScreen(business: business),
      ),
      (
        'Follow up and learn',
        Icons.psychology_alt_outlined,
        'What we know',
        'What Vyterlix has learned about your business, and your limits on suggestions.',
        (_) => MemoryScreen(business: business),
      ),
      (
        'Your business',
        Icons.storage_rounded,
        'Your data',
        'How complete your data is, and bring in more from a file.',
        (_) => DataScreen(business: business),
      ),
      (
        'Your business',
        Icons.credit_card_outlined,
        'Plan and billing',
        'Your plan, what you use, and what you have been charged.',
        (_) => BillingScreen(business: business),
      ),
      (
        'Your business',
        Icons.settings_outlined,
        'Settings',
        'Your business settings, your notifications and your team.',
        (_) => SettingsScreen(business: business),
      ),
    ];
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        for (final (i, (group, icon, title, subtitle, builder))
            in entries.indexed) ...[
          if (i == 0 || entries[i - 1].$1 != group) SectionTitle(group),
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
