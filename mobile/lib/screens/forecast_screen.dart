import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_insights.dart';
import '../theme.dart';
import '../widgets/charts.dart';
import '../widgets/common.dart';

const _stockText = {
  'order_now': 'Order now',
  'watch': 'Keep an eye',
  'ok': 'Fine',
};

Color _stockColor(String status) {
  switch (status) {
    case 'order_now':
      return Palette.bad;
    case 'watch':
      return Palette.warn;
    default:
      return Palette.ok;
  }
}

/// What to expect over the next few months, with a range around each, and what to stock.
class ForecastScreen extends StatefulWidget {
  const ForecastScreen({super.key, required this.business});

  final Business business;

  @override
  State<ForecastScreen> createState() => _ForecastScreenState();
}

class _ForecastScreenState extends State<ForecastScreen> {
  List<ForecastFigure>? _figures;
  String? _code;
  Forecast? _forecast;
  ForecastAccuracy? _accuracy;
  StockRequirements? _stock;
  bool _forecastLoaded = false;
  bool _busy = false;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/forecasts';

  bool get _canWork => widget.business.role != 'viewer';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final options = await api.get(_base) as Map<String, dynamic>;
      final figures = [
        for (final f in (options['figures'] as List))
          ForecastFigure.fromJson(f as Map<String, dynamic>),
      ];
      if (!mounted) return;
      setState(() {
        _figures = figures;
        _code ??= figures.isEmpty ? null : figures.first.code;
      });
      await Future.wait([_loadForecast(), _loadStock()]);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _loadForecast() async {
    final code = _code;
    if (code == null) return;
    final api = context.read<ApiClient>();
    try {
      final data = await api.get('$_base/$code');
      ForecastAccuracy? accuracy;
      if (data != null) {
        try {
          accuracy = ForecastAccuracy.fromJson(
            await api.get('$_base/$code/accuracy') as Map<String, dynamic>,
          );
        } on ApiException {
          accuracy = null; // the range and the months still show without it
        }
      }
      if (!mounted) return;
      setState(() {
        _forecast = data == null
            ? null
            : Forecast.fromJson(data as Map<String, dynamic>);
        _accuracy = accuracy;
        _forecastLoaded = true;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _loadStock() async {
    try {
      final data = await context.read<ApiClient>().get(
        '$_base/stock-requirements',
      );
      if (mounted) {
        setState(
          () =>
              _stock = StockRequirements.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException {
      if (mounted) {
        setState(
          () => _stock = null,
        ); // no stock records: the section is left out
      }
    }
  }

  void _choose(String code) {
    setState(() {
      _code = code;
      _forecast = null;
      _accuracy = null;
      _forecastLoaded = false;
    });
    _loadForecast();
  }

  Future<void> _workOut() async {
    setState(() => _busy = true);
    final api = context.read<ApiClient>();
    try {
      await api.post('$_base/$_code');
      await _loadForecast();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Forecast',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final figures = _figures;
    if (figures == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final f in figures) ...[
                  ChoiceChip(
                    label: Text(f.name),
                    selected: _code == f.code,
                    showCheckmark: false,
                    onSelected: (_) => _choose(f.code),
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
          if (!_forecastLoaded)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_forecast == null) ...[
            Text(
              'This forecast has not been worked out yet.',
              style: TextStyle(color: muted),
            ),
            if (_canWork) ...[
              const SizedBox(height: 12),
              FilledButton(
                onPressed: _busy ? null : _workOut,
                child: const Text('Work it out now'),
              ),
            ],
          ] else
            ..._forecastView(_forecast!, muted),
          if (_stock != null && _stock!.rows.isNotEmpty)
            ..._stockView(_stock!, muted),
        ],
      ),
    );
  }

  List<Widget> _forecastView(Forecast f, Color muted) {
    if (f.status != 'ok') {
      return [Text(f.explanation, style: TextStyle(color: muted))];
    }
    return [
      Text(
        '${f.name}: the next ${f.predictions.length} months',
        style: Theme.of(context).textTheme.titleMedium
            ?.copyWith(fontWeight: FontWeight.w700),
      ),
      const SizedBox(height: 8),
      Text(f.explanation),
      const SizedBox(height: 16),
      Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              LineChart(
                key: const ValueKey('forecast-chart'),
                history: [for (final h in f.history) h.$2],
                expected: [for (final p in f.predictions) p.value],
                lower: [for (final p in f.predictions) p.lower],
                upper: [for (final p in f.predictions) p.upper],
                actual: [for (final p in f.predictions) p.actual],
              ),
              const SizedBox(height: 8),
              Text(
                'Solid: what happened. Dashed: what we expect. Shaded: the range it should land in, ${f.intervalLevel} times out of 100.',
                style: TextStyle(color: muted, fontSize: 12),
              ),
            ],
          ),
        ),
      ),
      const SectionTitle('Month by month'),
      for (final p in f.predictions) ...[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  monthName(p.periodStart),
                  style: const TextStyle(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                Text('We expect ${_fmt(p.value, f.unit)}'),
                Text(
                  'Most likely between ${_fmt(p.lower, f.unit)} and ${_fmt(p.upper, f.unit)}',
                  style: TextStyle(color: muted),
                ),
                Text(
                  p.actual == null
                      ? 'Not finished yet'
                      : 'What happened: ${_fmt(p.actual!, f.unit)}',
                  style: TextStyle(color: muted),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 12),
      ],
      if (_accuracy != null) ...[
        const SectionTitle('How accurate it has been'),
        Text(_accuracy!.headline),
        if (_accuracy!.verdict != null) ...[
          const SizedBox(height: 4),
          Text(_accuracy!.verdict!, style: TextStyle(color: muted)),
        ],
      ],
      if (f.methodName != null) ...[
        const SizedBox(height: 12),
        Text(
          'Method: ${f.methodName}',
          style: TextStyle(color: muted, fontSize: 12),
        ),
      ],
    ];
  }

  String _fmt(double value, String unit) => formatValue(value.toString(), unit);

  List<Widget> _stockView(StockRequirements s, Color muted) => [
    const SectionTitle('What to stock'),
    Text(s.headline, style: const TextStyle(fontWeight: FontWeight.w600)),
    const SizedBox(height: 4),
    Text(s.note, style: TextStyle(color: muted, fontSize: 13)),
    const SizedBox(height: 12),
    for (final row in s.rows) ...[
      Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      row.name,
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ),
                  Tag(
                    _stockText[row.status] ?? row.status,
                    color: _stockColor(row.status),
                  ),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                'Expect to sell ${row.expectedUnits}. In stock: ${row.onHand}${row.daysOfCover == null ? '' : ', which lasts about ${row.daysOfCover} days'}.',
                style: TextStyle(color: muted),
              ),
              if (row.status != 'ok')
                Text(
                  'Order about ${row.orderSuggested} to cover a busy month.',
                  style: const TextStyle(fontWeight: FontWeight.w600),
                ),
            ],
          ),
        ),
      ),
      const SizedBox(height: 8),
    ],
  ];
}
