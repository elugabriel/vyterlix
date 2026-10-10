import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';

const _vatChoices = [
  ('20', '20% VAT (standard)'),
  ('5', '5% VAT (reduced)'),
  ('0', '0% (no VAT)'),
  ('amount', 'I\'ll type the VAT amount'),
];

String _today() {
  final now = DateTime.now();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${now.year}-${two(now.month)}-${two(now.day)}';
}

/// Type in a single sale or expense (the quick version of the website's "Type in data").
class EntryScreen extends StatefulWidget {
  const EntryScreen({super.key, required this.business});

  final Business business;

  @override
  State<EntryScreen> createState() => _EntryScreenState();
}

class _EntryScreenState extends State<EntryScreen> {
  bool _isSale = true;
  bool _refund = false; // a refund (sale) or a credit note (expense)
  String _date = _today();
  final _amount = TextEditingController();
  final _vatAmount = TextEditingController();
  final _text =
      TextEditingController(); // reference (sale) or what it was for (expense)
  bool _includesVat = true;
  String _vat = '20';
  List<(String, String)> _channels = [];
  List<(String, String)> _categories = [];
  String? _pick; // sales channel or cost category id
  bool _busy = false;
  String? _error;
  String? _saved;

  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _loadLists();
  }

  @override
  void dispose() {
    _amount.dispose();
    _vatAmount.dispose();
    _text.dispose();
    super.dispose();
  }

  Future<List<(String, String)>> _list(String name) async {
    try {
      final data = await context.read<ApiClient>().get('$_org/lists/$name');
      return [
        for (final i in data as List)
          if ((i as Map<String, dynamic>)['is_active'] == true)
            (i['id'] as String, i['name'] as String),
      ];
    } on ApiException {
      return []; // these are optional, so the form still works without them
    }
  }

  Future<void> _loadLists() async {
    final channels = await _list('sales_channel');
    final categories = await _list('cost_category');
    if (mounted) {
      setState(() {
        _channels = channels;
        _categories = categories;
      });
    }
  }

  Future<void> _chooseDate() async {
    final current = DateTime.parse(_date);
    final picked = await showDatePicker(
      context: context,
      initialDate: current,
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
    );
    if (picked != null) {
      setState(() {
        _date =
            '${picked.year}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
      });
    }
  }

  Future<void> _save() async {
    final amount = _amount.text.trim();
    if (double.tryParse(amount) == null || double.parse(amount) <= 0) {
      setState(() => _error = 'Type the amount in pounds, for example 12.50.');
      return;
    }
    final text = _text.text.trim();
    final body = <String, dynamic>{
      _isSale ? 'sold_on' : 'spent_on': _date,
      'kind': _isSale
          ? (_refund ? 'refund' : 'sale')
          : (_refund ? 'credit' : 'expense'),
      'amount': amount,
      'amount_includes_vat': _includesVat,
      if (_vat == 'amount')
        'vat_amount': _vatAmount.text.trim().isEmpty
            ? '0'
            : _vatAmount.text.trim()
      else
        'vat_rate': _vat,
      if (_isSale) ...{
        'sales_channel_id': _pick,
        'reference': text.isEmpty ? null : text,
        'lines': <dynamic>[],
      } else ...{
        'cost_category_id': _pick,
        'description': text.isEmpty ? null : text,
      },
    };
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    try {
      final data = await context.read<ApiClient>().post(
        '$_org/${_isSale ? 'sales' : 'expenses'}',
        body: body,
      ) as Map<String, dynamic>;
      if (!mounted) return;
      final total = data['gross_amount'] as String?;
      setState(() {
        _saved =
            '${_isSale ? 'Sale' : 'Expense'} saved${total == null ? '' : ': ${gbp(total)} in total'}.';
        _amount.clear();
        _vatAmount.clear();
        _text.clear();
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final options = _isSale ? _channels : _categories;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Type in a sale or expense',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: true, label: Text('Sale')),
              ButtonSegment(value: false, label: Text('Expense')),
            ],
            selected: {_isSale},
            onSelectionChanged: (s) => setState(() {
              _isSale = s.first;
              _refund = false;
              _pick = null;
              _saved = null;
              _error = null;
            }),
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  OutlinedButton.icon(
                    key: const ValueKey('entry-date'),
                    onPressed: _chooseDate,
                    icon: const Icon(Icons.calendar_today_rounded, size: 18),
                    label: Text(
                      '${_isSale ? 'Date of sale' : 'Date'}: ${ukDate(_date)}',
                    ),
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(
                      _isSale
                          ? 'This is a refund'
                          : 'This is a credit note (money back)',
                    ),
                    value: _refund,
                    onChanged: (v) => setState(() => _refund = v),
                  ),
                  TextField(
                    controller: _amount,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(labelText: 'Amount (£)'),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Does that amount include VAT?',
                    style: TextStyle(color: muted),
                  ),
                  RadioGroup<bool>(
                    groupValue: _includesVat,
                    onChanged: (v) => setState(() => _includesVat = v ?? true),
                    child: const Column(
                      children: [
                        RadioListTile<bool>(
                          contentPadding: EdgeInsets.zero,
                          title: Text('Yes, it includes VAT'),
                          value: true,
                        ),
                        RadioListTile<bool>(
                          contentPadding: EdgeInsets.zero,
                          title: Text('No, it\'s before VAT'),
                          value: false,
                        ),
                      ],
                    ),
                  ),
                  DropdownButtonFormField<String>(
                    key: const ValueKey('entry-vat'),
                    initialValue: _vat,
                    decoration: const InputDecoration(labelText: 'VAT'),
                    items: [
                      for (final (value, label) in _vatChoices)
                        DropdownMenuItem(value: value, child: Text(label)),
                    ],
                    onChanged: (v) => setState(() => _vat = v ?? '20'),
                  ),
                  if (_vat == 'amount')
                    TextField(
                      controller: _vatAmount,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      decoration: const InputDecoration(
                        labelText: 'VAT amount (£)',
                      ),
                    ),
                  if (options.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String?>(
                      key: ValueKey('entry-pick-$_isSale'),
                      initialValue: _pick,
                      decoration: InputDecoration(
                        labelText: _isSale
                            ? 'Sales channel (optional)'
                            : 'Cost category (optional)',
                      ),
                      items: [
                        DropdownMenuItem<String?>(
                          value: null,
                          child: const Text('Not set'),
                        ),
                        for (final (id, name) in options)
                          DropdownMenuItem<String?>(
                            value: id,
                            child: Text(name),
                          ),
                      ],
                      onChanged: (v) => setState(() => _pick = v),
                    ),
                  ],
                  const SizedBox(height: 8),
                  TextField(
                    controller: _text,
                    maxLength: _isSale ? 200 : 300,
                    decoration: InputDecoration(
                      labelText: _isSale
                          ? 'Reference (optional)'
                          : 'What it was for (optional)',
                      helperText: _isSale
                          ? 'For example an invoice or receipt number. Each can only be used once.'
                          : null,
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ),
          if (_saved != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(_saved!),
            ),
          FilledButton(
            onPressed: _busy ? null : _save,
            child: Text(_isSale ? 'Save sale' : 'Save expense'),
          ),
        ],
      ),
    );
  }
}
