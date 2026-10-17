import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../models.dart';

const _stages = [
  ('lead', 'A lead (an enquiry)'),
  ('proposal', 'Proposal sent'),
  ('won', 'Won'),
  ('lost', 'Lost'),
];

/// Type in one lead, proposal or renewal (the quick version of the website's "Lead or proposal").
/// The days default to today on the server: the day it came in, the day the proposal was sent, the
/// day it was won or lost.
class LeadScreen extends StatefulWidget {
  const LeadScreen({super.key, required this.business});

  final Business business;

  @override
  State<LeadScreen> createState() => _LeadScreenState();
}

class _LeadScreenState extends State<LeadScreen> {
  final _name = TextEditingController();
  final _value = TextEditingController();
  final _notes = TextEditingController();
  String _stage = 'lead';
  bool _renewal = false;
  String? _customer;
  List<(String, String)> _customers = [];
  bool _busy = false;
  String? _error;
  String? _saved;

  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _loadCustomers();
  }

  @override
  void dispose() {
    _name.dispose();
    _value.dispose();
    _notes.dispose();
    super.dispose();
  }

  Future<void> _loadCustomers() async {
    try {
      final data = await context.read<ApiClient>().get(
        '$_org/customers',
        query: {'limit': '100'},
      );
      final found = [
        for (final c in (data as Map<String, dynamic>)['items'] as List)
          (
            (c as Map<String, dynamic>)['id'] as String,
            (c['name'] ?? c['email'] ?? 'A client') as String,
          ),
      ];
      if (mounted) setState(() => _customers = found);
    } on ApiException {
      // the form still works
    }
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => _error = 'Say what it is, for example website for Acme.');
      return;
    }
    final value = _value.text.trim();
    if (value.isNotEmpty && double.tryParse(value) == null) {
      setState(
        () => _error = 'Type what it is worth in pounds, for example 4000.',
      );
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    try {
      await context.read<ApiClient>().post(
        '$_org/opportunities',
        body: {
          'name': name,
          'stage': _stage,
          'kind': _renewal ? 'renewal' : 'new',
          'value': value.isEmpty ? null : value,
          'customer_id': _customer,
          'notes': _notes.text.trim().isEmpty ? null : _notes.text.trim(),
        },
      );
      if (!mounted) return;
      _name.clear();
      _value.clear();
      _notes.clear();
      setState(() => _saved = 'Saved.');
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Type in a lead or proposal',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  TextField(
                    controller: _name,
                    maxLength: 200,
                    decoration: const InputDecoration(
                      labelText: 'What is it?',
                      helperText: 'For example: website for Acme.',
                    ),
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: _stage,
                    decoration: const InputDecoration(
                      labelText: 'Where is it up to?',
                    ),
                    items: [
                      for (final (value, label) in _stages)
                        DropdownMenuItem(value: value, child: Text(label)),
                    ],
                    onChanged: (v) => setState(() => _stage = v ?? _stage),
                  ),
                  SwitchListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('A client renewing'),
                    value: _renewal,
                    onChanged: (v) => setState(() => _renewal = v),
                  ),
                  TextField(
                    controller: _value,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Worth (£, before VAT, optional)',
                    ),
                  ),
                  if (_customers.isNotEmpty) ...[
                    const SizedBox(height: 8),
                    DropdownButtonFormField<String?>(
                      initialValue: _customer,
                      decoration: const InputDecoration(
                        labelText: 'Client (optional)',
                      ),
                      items: [
                        const DropdownMenuItem<String?>(
                          value: null,
                          child: Text('Not recorded'),
                        ),
                        for (final (id, name) in _customers)
                          DropdownMenuItem<String?>(
                            value: id,
                            child: Text(name),
                          ),
                      ],
                      onChanged: (v) => setState(() => _customer = v),
                    ),
                  ],
                  TextField(
                    controller: _notes,
                    maxLength: 300,
                    decoration: const InputDecoration(
                      labelText: 'Notes (optional)',
                    ),
                  ),
                  Text(
                    'The dates are set to today. Change them on the website if you need to.',
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                  if (_error != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        _error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  if (_saved != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(_saved!),
                    ),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: _busy ? null : _save,
                    style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(50),
                    ),
                    child: Text(_busy ? 'Saving...' : 'Save'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
