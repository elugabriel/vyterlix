import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/common.dart';

String usageText(Map<String, dynamic> f) {
  if (f['enabled'] != true) return 'Not included';
  final used = f['used'];
  if (used == null) return 'Included';
  final limit = f['limit'];
  return limit == null ? '$used in use, no limit' : '$used of $limit in use';
}

/// What the business is on, what it uses, the plans, and what has been charged. Starting to pay
/// happens on the website, on the payment provider's own page; the app never sees card details.
class BillingScreen extends StatefulWidget {
  const BillingScreen({super.key, required this.business});

  final Business business;

  @override
  State<BillingScreen> createState() => _BillingScreenState();
}

class _BillingScreenState extends State<BillingScreen> {
  Map<String, dynamic>? _billing;
  List<Map<String, dynamic>> _plans = [];
  List<Map<String, dynamic>>? _invoices; // null when not allowed to see them
  String? _error;
  String? _problem;
  String? _note;
  bool _busy = false;
  String _interval = 'month';

  String get _base => '/organizations/${widget.business.id}/billing';

  @override
  void initState() {
    super.initState();
    _load();
  }

  List<Map<String, dynamic>> _maps(dynamic data) => [
    for (final i in data as List) i as Map<String, dynamic>,
  ];

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final billing = await api.get(_base) as Map<String, dynamic>;
      final plans = _maps(await api.get('$_base/plans'));
      List<Map<String, dynamic>>? invoices;
      try {
        invoices = _maps(await api.get('$_base/invoices'));
      } on ApiException catch (error) {
        if (error.status != 403) rethrow;
      }
      if (!mounted) return;
      setState(() {
        _billing = billing;
        _plans = plans;
        _invoices = invoices;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _act(String path, String done, {Object? body}) async {
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      final result = await context.read<ApiClient>().post(
        '$_base/$path',
        body: body,
      );
      var note = done;
      if (result is Map<String, dynamic> && result['result'] != null) {
        note = result['result'] == 'checkout'
            ? 'To finish this change, open Vyterlix on the website and pay there.'
            : '${result['message']}';
      }
      await _load();
      if (mounted) setState(() => _note = note);
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _cancel() async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Cancel my plan?'),
        content: const Text(
          'Your plan will end when the period you have paid for does.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Cancel it'),
          ),
        ],
      ),
    );
    if (yes == true) {
      await _act(
        'cancel',
        'Your plan will end when the period you have paid for does.',
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final b = _billing;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Plan and billing',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : b == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  if (_problem != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        _problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  if (_note != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(_note!),
                    ),
                  _current(b, muted),
                  const SectionTitle('What you use'),
                  for (final f in _maps(b['features'])) _feature(f, muted),
                  const SectionTitle('Plans'),
                  SegmentedButton<String>(
                    segments: const [
                      ButtonSegment(value: 'month', label: Text('Pay monthly')),
                      ButtonSegment(value: 'year', label: Text('Pay yearly')),
                    ],
                    selected: {_interval},
                    onSelectionChanged: (s) =>
                        setState(() => _interval = s.first),
                  ),
                  const SizedBox(height: 12),
                  for (final p in _plans) _plan(p, b, muted),
                  Text(
                    'Prices exclude VAT (20%), which is added when you pay.',
                    style: TextStyle(color: muted, fontSize: 13),
                  ),
                  const SectionTitle('What you have been charged'),
                  ..._invoiceList(muted),
                ],
              ),
            ),
    );
  }

  Widget _current(Map<String, dynamic> b, Color muted) {
    final sub = b['subscription'] as Map<String, dynamic>;
    final canManage = b['can_manage'] == true;
    final status = sub['status'];
    final paying = status == 'active' || status == 'past_due';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'YOUR PLAN',
              style: TextStyle(
                color: muted,
                fontSize: 12,
                fontWeight: FontWeight.w700,
                letterSpacing: 1,
              ),
            ),
            const SizedBox(height: 6),
            Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                  '${sub['plan_name']}',
                  style: const TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                Tag('${sub['status_label']}'),
              ],
            ),
            const SizedBox(height: 8),
            Text('${sub['message']}'),
            if (sub['scheduled_plan_name'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Moving to the ${sub['scheduled_plan_name']} plan when this period ends.',
                ),
              ),
            if (sub['current_period_end'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(
                  'Current period: ${ukDate('${sub['current_period_start']}'.substring(0, 10))} to ${ukDate('${sub['current_period_end']}'.substring(0, 10))}',
                  style: TextStyle(color: muted),
                ),
              ),
            if (canManage && paying) ...[
              const SizedBox(height: 12),
              if (sub['cancel_at_period_end'] == true)
                OutlinedButton(
                  onPressed: _busy
                      ? null
                      : () =>
                            _act('resume', 'Your plan will carry on renewing.'),
                  child: const Text('Keep my plan'),
                )
              else
                OutlinedButton(
                  onPressed: _busy ? null : _cancel,
                  child: const Text('Cancel my plan'),
                ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _feature(Map<String, dynamic> f, Color muted) {
    final limit = f['limit'];
    final used = f['used'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${f['label']}',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            Text(usageText(f), style: TextStyle(color: muted)),
            if (f['enabled'] == true && limit != null && used != null) ...[
              const SizedBox(height: 8),
              ClipRRect(
                borderRadius: BorderRadius.circular(999),
                child: LinearProgressIndicator(
                  value: ((used as num) / ((limit as num) == 0 ? 1 : limit))
                      .clamp(0, 1)
                      .toDouble(),
                  minHeight: 6,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _plan(Map<String, dynamic> p, Map<String, dynamic> b, Color muted) {
    final price = _interval == 'year' ? p['price_year'] : p['price_month'];
    final sub = b['subscription'] as Map<String, dynamic>;
    final paying = sub['status'] == 'active' || sub['status'] == 'past_due';
    final current = p['current'] == true;
    Widget? action;
    if (b['can_manage'] != true) {
      action = null;
    } else if (p['self_serve'] != true) {
      action = Text('${p['vat_note'] ?? ''}', style: TextStyle(color: muted));
    } else if (price == null) {
      action = Text('Not sold this way', style: TextStyle(color: muted));
    } else if (current &&
        sub['interval'] == _interval &&
        sub['scheduled_plan_name'] == null) {
      action = Text('Your plan', style: TextStyle(color: muted));
    } else if (paying) {
      action = FilledButton(
        key: ValueKey('move-${p['code']}'),
        onPressed: _busy
            ? null
            : () => _act(
                'change',
                'Your plan has been changed.',
                body: {'plan_code': p['code'], 'interval': _interval},
              ),
        child: Text('Move to ${p['name']}'),
      );
    } else {
      action = Text(
        'To choose ${p['name']}, open Vyterlix on the website and pay there.',
        style: TextStyle(color: muted),
      );
    }
    return Card(
      shape: current
          ? RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Palette.accent, width: 1.5),
            )
          : null,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                  '${p['name']}',
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 17,
                  ),
                ),
                if (current) const Tag('Current'),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              price == null ? 'Talk to us' : '$price a $_interval',
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700),
            ),
            if (_interval == 'year' && p['year_saving'] != null)
              Text(
                'Saves ${p['year_saving']} a year',
                style: TextStyle(color: muted),
              ),
            const SizedBox(height: 6),
            Text('${p['description']}', style: TextStyle(color: muted)),
            const SizedBox(height: 6),
            for (final f in _maps(p['features']))
              Text('${f['label']}: ${f['text']}'),
            if (action != null) ...[const SizedBox(height: 12), action],
          ],
        ),
      ),
    );
  }

  List<Widget> _invoiceList(Color muted) {
    final invoices = _invoices;
    if (invoices == null) {
      return [
        Text(
          'Only the owner can see what has been charged.',
          style: TextStyle(color: muted),
        ),
      ];
    }
    if (invoices.isEmpty) {
      return [
        Text('Nothing has been charged yet.', style: TextStyle(color: muted)),
      ];
    }
    return [
      for (final i in invoices)
        Card(
          child: ListTile(
            title: Text(
              '${i['number'] ?? '-'} · ${i['total']}',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            subtitle: Text(
              '${ukDate('${i['issued_at']}'.substring(0, 10))} · ${_maps(i['lines']).map((l) => l['description']).join('; ')}\nNet ${i['net']} · VAT ${i['vat']} · ${i['status_label']}',
            ),
            isThreeLine: true,
          ),
        ),
    ];
  }
}
