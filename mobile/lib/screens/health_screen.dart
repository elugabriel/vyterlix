import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_insights.dart';
import '../theme.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';
import '../widgets/severity.dart';

const _arrow = {'up': '▲', 'down': '▼', 'flat': '•'};

/// One score for how the business is doing, why, and the areas behind it.
class HealthScreen extends StatefulWidget {
  const HealthScreen({super.key, required this.business});

  final Business business;

  @override
  State<HealthScreen> createState() => _HealthScreenState();
}

class _HealthScreenState extends State<HealthScreen> {
  HealthReport? _report;
  List<HealthPoint> _history = const [];
  bool _loaded = false;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/business-health';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final results = await Future.wait([
        api.get(_base),
        api.get('$_base/history'),
      ]);
      if (!mounted) return;
      final report = results[0];
      final points = (results[1] as Map<String, dynamic>)['points'] as List;
      setState(() {
        _report = report is Map<String, dynamic>
            ? HealthReport.fromJson(report)
            : null;
        _history = [
          for (final p in points)
            HealthPoint.fromJson(p as Map<String, dynamic>),
        ];
        _loaded = true;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Business health',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    if (!_loaded) return const Center(child: CircularProgressIndicator());
    final report = _report;
    if (report == null) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
            'Your business health has not been worked out yet. It appears once your figures have.',
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final scored = [
      for (final p in _history)
        if (p.score != null) p.score!.toDouble(),
    ];
    final change = report.previousScore == null || report.overallScore == null
        ? null
        : report.overallScore == report.previousScore
        ? 'no change on the month before'
        : '${report.overallScore! > report.previousScore! ? 'up' : 'down'} from ${report.previousScore} the month before';
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Container(
            padding: const EdgeInsets.all(24),
            decoration: BoxDecoration(
              gradient: Palette.gradient,
              borderRadius: BorderRadius.circular(20),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      report.overallScore == null
                          ? '–'
                          : '${report.overallScore}',
                      key: const ValueKey('health-overall'),
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 58,
                        fontWeight: FontWeight.w800,
                        height: 1,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        'out of 100',
                        style: TextStyle(
                          color: Colors.white.withValues(alpha: 0.7),
                        ),
                      ),
                    ),
                    const Spacer(),
                    Tag(
                      healthText[report.status] ?? report.status,
                      onDark: true,
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(
                  '${change == null ? '' : '${report.trend != null ? '${_arrow[report.trend]} ' : ''}$change · '}${monthName(report.periodStart)}',
                  style: TextStyle(color: Colors.white.withValues(alpha: 0.85)),
                ),
                const SizedBox(height: 12),
                Text(
                  report.explanation,
                  style: const TextStyle(color: Colors.white),
                ),
              ],
            ),
          ),
          if (report.coveragePct < 100)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'This score covers ${report.coveragePct}% of the picture: the rest could not be measured yet.',
                style: TextStyle(color: muted),
              ),
            ),
          if (scored.length > 1) ...[
            const SectionTitle('Month by month'),
            Card(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Sparkline(
                  key: const ValueKey('health-history'),
                  values: scored,
                  height: 80,
                ),
              ),
            ),
            const SizedBox(height: 4),
            Text(
              '${monthName(_history.first.periodStart)} to ${monthName(_history.last.periodStart)}',
              style: TextStyle(color: muted, fontSize: 12),
            ),
          ],
          const SectionTitle('The areas behind it'),
          for (final component in report.components) ...[
            _ComponentCard(component: component),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}

class _ComponentCard extends StatelessWidget {
  const _ComponentCard({required this.component});

  final HealthComponent component;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final color = healthColor(component.status);
    return Card(
      child: ExpansionTile(
        shape: const Border(),
        collapsedShape: const Border(),
        tilePadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        title: Row(
          children: [
            Expanded(
              child: Text(
                component.label,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
            ),
            Text(
              component.score == null ? '–' : '${component.score}',
              style: TextStyle(
                fontWeight: FontWeight.w800,
                fontSize: 20,
                color: color,
              ),
            ),
            Text(' / 100', style: TextStyle(color: muted, fontSize: 12)),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 6),
          child: Wrap(
            spacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Tag(
                healthText[component.status] ?? component.status,
                color: color,
              ),
              if (component.trend != null)
                Text(
                  _arrow[component.trend]!,
                  style: TextStyle(
                    color: component.trend == 'up'
                        ? Palette.ok
                        : component.trend == 'down'
                        ? Palette.bad
                        : muted,
                  ),
                ),
            ],
          ),
        ),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(component.explanation),
          const SizedBox(height: 12),
          for (final metric in component.metrics) ...[
            Row(
              children: [
                Expanded(
                  child: Text(
                    metric.name,
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                ),
                Text(
                  metric.score.toStringAsFixed(0),
                  style: TextStyle(
                    color: goodFairPoor(metric.score),
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: metric.score / 100,
                minHeight: 6,
                color: goodFairPoor(metric.score),
              ),
            ),
            const SizedBox(height: 4),
            Text(metric.text, style: TextStyle(color: muted, fontSize: 13)),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}
