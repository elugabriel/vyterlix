import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../auth/auth_controller.dart';
import '../format.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/common.dart';

const _healthText = {
  'healthy': 'Healthy',
  'fair': 'Fair',
  'needs_attention': 'Needs attention',
  'at_risk': 'At risk',
  'not_enough_data': 'Not enough data',
};
const _severityText = {
  'info': 'For information',
  'low': 'Low',
  'medium': 'Medium',
  'high': 'High',
  'critical': 'Critical',
};
const _kindText = {
  'approval': 'Waiting for you',
  'action_overdue': 'Late',
  'alert': 'Alert',
  'follow_up': 'Check the result',
  'action_due_soon': 'Due soon',
  'suggestion': 'Suggestion',
};

Color _severityColor(String severity) {
  switch (severity) {
    case 'critical':
    case 'high':
      return Palette.bad;
    case 'medium':
      return Palette.warn;
    default:
      return Palette.lightMuted;
  }
}

/// The front screen: what needs attention first, then how the business is doing.
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key, required this.business});

  final Business business;

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  Dashboard? _dashboard;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(
        '/organizations/${widget.business.id}/dashboard',
      );
      if (mounted) {
        setState(
          () => _dashboard = Dashboard.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.business.name,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        actions: [
          IconButton(
            tooltip: 'Switch business',
            icon: const Icon(Icons.swap_horiz_rounded),
            onPressed: () => Navigator.of(context).pop(),
          ),
          PopupMenuButton<String>(
            tooltip: 'Account',
            icon: const Icon(Icons.account_circle_outlined),
            onSelected: (value) async {
              if (value == 'logout') {
                final navigator = Navigator.of(context);
                await auth.logout();
                navigator.popUntil((route) => route.isFirst);
              }
            },
            itemBuilder: (_) => [
              PopupMenuItem<String>(
                enabled: false,
                child: Text(auth.user?.email ?? ''),
              ),
              const PopupMenuItem<String>(
                value: 'logout',
                child: Text('Log out'),
              ),
            ],
          ),
        ],
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final dashboard = _dashboard;
    if (dashboard == null) {
      return const Center(child: CircularProgressIndicator());
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          _Hero(dashboard: dashboard),
          if (dashboard.setup != null) ...[
            const SizedBox(height: 16),
            _SetupCard(setup: dashboard.setup!),
          ],
          const SectionTitle('Needs your attention'),
          if (dashboard.attention.isEmpty)
            Text(
              'Nothing needs you right now.',
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          for (final item in dashboard.attention) ...[
            _AttentionCard(item: item),
            const SizedBox(height: 12),
          ],
          if (dashboard.moreAttention > 0)
            Text(
              '${dashboard.moreAttention} more. See them all on the Alerts page of the website.',
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          if (dashboard.figures.isNotEmpty) ...[
            const SectionTitle('Your key figures'),
            _Figures(figures: dashboard.figures),
          ],
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

class _Hero extends StatelessWidget {
  const _Hero({required this.dashboard});

  final Dashboard dashboard;

  @override
  Widget build(BuildContext context) {
    final health = dashboard.health;
    final score = health?.score;
    String? change;
    if (health != null && score != null && health.previousScore != null) {
      change = score == health.previousScore
          ? 'No change on the month before'
          : '${score > health.previousScore! ? 'Up' : 'Down'} from ${health.previousScore} the month before';
    }
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        gradient: Palette.gradient,
        borderRadius: BorderRadius.circular(20),
        boxShadow: [
          BoxShadow(
            color: const Color(0xFF3730A3).withValues(alpha: 0.35),
            blurRadius: 28,
            offset: const Offset(0, 14),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'TODAY',
            style: TextStyle(
              color: Colors.white.withValues(alpha: 0.7),
              fontSize: 12,
              fontWeight: FontWeight.w700,
              letterSpacing: 1.2,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            dashboard.headline,
            style: const TextStyle(
              color: Colors.white,
              fontSize: 22,
              fontWeight: FontWeight.w700,
              height: 1.25,
            ),
          ),
          if (health != null && health.weakest != null) ...[
            const SizedBox(height: 8),
            Text(
              'The area pulling your health down most is ${health.weakest}.',
              style: TextStyle(color: Colors.white.withValues(alpha: 0.85)),
            ),
          ],
          if (health != null && score != null) ...[
            const SizedBox(height: 20),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  '$score',
                  key: const ValueKey('health-score'),
                  style: const TextStyle(
                    color: Colors.white,
                    fontSize: 54,
                    fontWeight: FontWeight.w800,
                    height: 1,
                  ),
                ),
                const SizedBox(width: 8),
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    'out of 100',
                    style: TextStyle(
                      color: Colors.white.withValues(alpha: 0.7),
                    ),
                  ),
                ),
                const Spacer(),
                Tag(_healthText[health.status] ?? health.status, onDark: true),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '${change == null ? '' : '$change · '}${monthName(health.period)}',
              style: TextStyle(color: Colors.white.withValues(alpha: 0.8)),
            ),
          ],
        ],
      ),
    );
  }
}

class _SetupCard extends StatelessWidget {
  const _SetupCard({required this.setup});

  final SetupGlance setup;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'Finish setting up',
              style: TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
            const SizedBox(height: 6),
            Text(
              '${setup.done} of ${setup.total} steps are done. Finish them on the website; the more you tell us, the better the figures.',
            ),
            const SizedBox(height: 12),
            ClipRRect(
              borderRadius: BorderRadius.circular(999),
              child: LinearProgressIndicator(
                value: setup.total == 0 ? 0 : setup.done / setup.total,
                minHeight: 8,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _AttentionCard extends StatelessWidget {
  const _AttentionCard({required this.item});

  final AttentionItem item;

  @override
  Widget build(BuildContext context) {
    final color = _severityColor(item.severity);
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(width: 4, color: color),
            Expanded(
              child: Padding(
                padding: const EdgeInsets.all(16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Wrap(
                      spacing: 8,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Tag(
                          _severityText[item.severity] ?? item.severity,
                          color: color,
                        ),
                        Text(
                          _kindText[item.kind] ?? item.kind,
                          style: TextStyle(color: muted, fontSize: 13),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    Text(
                      item.title,
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 4),
                    Text(item.detail, style: TextStyle(color: muted)),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Figures extends StatelessWidget {
  const _Figures({required this.figures});

  final List<FigureGlance> figures;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth > 620 ? 3 : 2;
        final width = (constraints.maxWidth - 12 * (columns - 1)) / columns;
        return Wrap(
          spacing: 12,
          runSpacing: 12,
          children: [
            for (final f in figures)
              SizedBox(
                width: width,
                child: _FigureCard(figure: f),
              ),
          ],
        );
      },
    );
  }
}

class _FigureCard extends StatelessWidget {
  const _FigureCard({required this.figure});

  final FigureGlance figure;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final change = figure.changePct;
    final good = figure.isGood;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(figure.name, style: TextStyle(color: muted, fontSize: 13)),
            const SizedBox(height: 4),
            Text(
              formatValue(figure.value, figure.unit),
              style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 4),
            if (change == null || figure.unit == 'percent')
              Text(
                monthName(figure.period),
                style: TextStyle(color: muted, fontSize: 12),
              )
            else
              Text(
                '${change >= 0 ? '▲' : '▼'} ${change.abs().toStringAsFixed(1)}% on the month before',
                style: TextStyle(
                  color: good == null
                      ? muted
                      : (good ? Palette.ok : Palette.bad),
                  fontSize: 12,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
