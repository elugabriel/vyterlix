import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_insights.dart';
import '../theme.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';

const _categoryTitle = {
  'financial': 'Money',
  'sales': 'Sales',
  'customer': 'Customers',
  'inventory': 'Stock',
};
const _categoryOrder = ['financial', 'sales', 'customer', 'inventory'];

/// Good news green, bad news red, nothing to say grey.
Color changeColor(BuildContext context, int sign, String direction) {
  if (sign == 0 || direction == 'neutral') {
    return Theme.of(context).colorScheme.onSurfaceVariant;
  }
  return (sign > 0) == (direction == 'up_good') ? Palette.ok : Palette.bad;
}

/// Every figure about the business, grouped, with the latest month and how it changed.
class KeyFiguresScreen extends StatefulWidget {
  const KeyFiguresScreen({super.key, required this.business});

  final Business business;

  @override
  State<KeyFiguresScreen> createState() => _KeyFiguresScreenState();
}

class _KeyFiguresScreenState extends State<KeyFiguresScreen> {
  List<Kpi>? _kpis;
  String? _workedOut;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/kpis';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data =
          await context.read<ApiClient>().get(_base) as Map<String, dynamic>;
      if (!mounted) return;
      final run = data['last_run'];
      setState(() {
        _kpis = [
          for (final k in (data['kpis'] as List))
            Kpi.fromJson(k as Map<String, dynamic>),
        ];
        _workedOut = run is Map<String, dynamic>
            ? run['finished_at'] as String?
            : null;
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
          'Key figures',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final kpis = _kpis;
    if (kpis == null) return const Center(child: CircularProgressIndicator());
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final groups = <String, List<Kpi>>{};
    for (final k in kpis) {
      groups.putIfAbsent(k.category, () => []).add(k);
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            _workedOut == null
                ? 'Your figures have not been worked out yet. Add your data on the website first.'
                : 'Worked out on ${ukDateTime(_workedOut!)}. They update by themselves after an import.',
            style: TextStyle(color: muted),
          ),
          for (final category in _categoryOrder)
            if (groups.containsKey(category)) ...[
              SectionTitle(_categoryTitle[category]!),
              LayoutBuilder(
                builder: (context, constraints) {
                  final columns = constraints.maxWidth > 620 ? 3 : 2;
                  final width =
                      (constraints.maxWidth - 12 * (columns - 1)) / columns;
                  return Wrap(
                    spacing: 12,
                    runSpacing: 12,
                    children: [
                      for (final k in groups[category]!)
                        SizedBox(
                          width: width,
                          child: _KpiCard(
                            kpi: k,
                            onTap: () => Navigator.of(context).push(
                              MaterialPageRoute<void>(
                                builder: (_) => KpiDetailScreen(
                                  business: widget.business,
                                  kpi: k,
                                ),
                              ),
                            ),
                          ),
                        ),
                    ],
                  );
                },
              ),
            ],
        ],
      ),
    );
  }
}

class _KpiCard extends StatelessWidget {
  const _KpiCard({required this.kpi, required this.onTap});

  final Kpi kpi;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final latest = kpi.latest;
    final change = latest == null
        ? null
        : changeSentence(
            status: latest.status,
            value: latest.value,
            previousValue: latest.previousValue,
            changePct: latest.changePct,
            unit: kpi.unit,
          );
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                kpi.name,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              if (latest == null || latest.status != 'ok')
                Text(
                  latest == null
                      ? 'Not worked out yet.'
                      : missingText(latest.status, kpi.requires),
                  style: TextStyle(color: muted, fontSize: 12),
                )
              else ...[
                Text(
                  formatValue(latest.value, kpi.unit),
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                Text(
                  monthName(latest.periodStart),
                  style: TextStyle(color: muted, fontSize: 12),
                ),
                if (change != null)
                  Text(
                    '${change.sign > 0
                        ? '▲'
                        : change.sign < 0
                        ? '▼'
                        : '•'} ${change.text}',
                    style: TextStyle(
                      color: changeColor(context, change.sign, kpi.direction),
                      fontSize: 12,
                    ),
                  ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

/// One figure over time: a chart, the months in a list, and how it is worked out.
class KpiDetailScreen extends StatefulWidget {
  const KpiDetailScreen({super.key, required this.business, required this.kpi});

  final Business business;
  final Kpi kpi;

  @override
  State<KpiDetailScreen> createState() => _KpiDetailScreenState();
}

class _KpiDetailScreenState extends State<KpiDetailScreen> {
  KpiHistory? _history;
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
        '/organizations/${widget.business.id}/kpis/${widget.kpi.code}',
        query: {'limit': '24'},
      );
      if (mounted) {
        setState(
          () => _history = KpiHistory.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.kpi.name,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final history = _history;
    if (history == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final ok = history.values.where((v) => v.status == 'ok').toList();
    final finished = ok.where((v) => v.isComplete).toList();
    final latest = finished.isEmpty ? null : finished.last;
    final newestFirst = history.values.reversed.toList();
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(history.description, style: TextStyle(color: muted)),
        const SizedBox(height: 16),
        if (latest != null) ...[
          Text(
            formatValue(latest.value, history.unit),
            style: Theme.of(context).textTheme.headlineMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          Text(monthName(latest.periodStart), style: TextStyle(color: muted)),
        ] else
          Text(
            history.values.isEmpty
                ? 'Not worked out yet.'
                : missingText(history.values.last.status, history.requires),
            style: TextStyle(color: muted),
          ),
        if (ok.isNotEmpty) ...[
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: BarChart(
                key: const ValueKey('kpi-chart'),
                values: [
                  for (final v in history.values)
                    v.status == 'ok' ? double.parse(v.value!) : null,
                ],
                lastIsPartial:
                    history.values.isNotEmpty &&
                    !history.values.last.isComplete,
              ),
            ),
          ),
        ],
        const SectionTitle('Month by month'),
        for (final v in newestFirst)
          Builder(
            builder: (context) {
              final change = changeSentence(
                status: v.status,
                value: v.value,
                previousValue: v.previousValue,
                changePct: v.changePct,
                unit: history.unit,
              );
              return Padding(
                padding: const EdgeInsets.symmetric(vertical: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text(
                        '${monthName(v.periodStart)}${v.isComplete ? '' : ' (so far)'}',
                        style: TextStyle(color: muted),
                      ),
                    ),
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.end,
                      children: [
                        Text(
                          v.status == 'ok'
                              ? formatValue(v.value, history.unit)
                              : '–',
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                        if (change != null)
                          Text(
                            '${change.sign > 0
                                ? '▲'
                                : change.sign < 0
                                ? '▼'
                                : '•'} ${change.text}',
                            style: TextStyle(
                              color: changeColor(
                                context,
                                change.sign,
                                history.direction,
                              ),
                              fontSize: 12,
                            ),
                          ),
                      ],
                    ),
                  ],
                ),
              );
            },
          ),
        const SizedBox(height: 8),
        ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: const Text('How it is worked out'),
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 12),
                child: Text(history.formula, style: TextStyle(color: muted)),
              ),
            ),
          ],
        ),
      ],
    );
  }
}
