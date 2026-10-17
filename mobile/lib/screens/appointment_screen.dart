import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';

const _statuses = [
  ('completed', 'Went ahead'),
  ('booked', 'Still to come'),
  ('cancelled', 'Cancelled'),
  ('no_show', 'Did not turn up'),
];

String _isoDay(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// Type in one appointment (the quick version of the website's Appointment tab).
class AppointmentScreen extends StatefulWidget {
  const AppointmentScreen({super.key, required this.business});

  final Business business;

  @override
  State<AppointmentScreen> createState() => _AppointmentScreenState();
}

class _AppointmentScreenState extends State<AppointmentScreen> {
  final _service = TextEditingController();
  final _staff = TextEditingController();
  final _notes = TextEditingController();
  String _day = _isoDay(DateTime.now());
  String _status = 'completed';
  String? _cancelledOn;
  String? _customer;
  List<(String, String)> _customers = [];
  List<String> _usedServices = [];
  List<String> _usedStaff = [];
  bool _busy = false;
  String? _error;
  String? _saved;

  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _loadChoices();
  }

  @override
  void dispose() {
    _service.dispose();
    _staff.dispose();
    _notes.dispose();
    super.dispose();
  }

  /// Who to pick and what has been used before. All optional: the form works without them.
  Future<void> _loadChoices() async {
    final api = context.read<ApiClient>();
    var customers = <(String, String)>[];
    var services = <String>[];
    var staff = <String>[];
    try {
      final data = await api.get('$_org/customers', query: {'limit': '100'});
      customers = [
        for (final c in (data as Map<String, dynamic>)['items'] as List)
          (
            (c as Map<String, dynamic>)['id'] as String,
            (c['name'] ?? c['email'] ?? 'A customer') as String,
          ),
      ];
    } on ApiException {
      // the form still works
    }
    try {
      final data =
          await api.get('$_org/bookings/choices') as Map<String, dynamic>;
      services = [for (final s in data['services'] as List) '$s'];
      staff = [for (final s in data['staff'] as List) '$s'];
    } on ApiException {
      // the form still works
    }
    if (mounted) {
      setState(() {
        _customers = customers;
        _usedServices = services;
        _usedStaff = staff;
      });
    }
  }

  Future<String?> _pickDay(String initial) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: DateTime.parse(initial),
      firstDate: DateTime(2000),
      lastDate: DateTime.now().add(const Duration(days: 366)),
    );
    return picked == null ? null : _isoDay(picked);
  }

  Future<void> _save() async {
    final service = _service.text.trim();
    if (service.isEmpty) {
      setState(() => _error = 'Say which service it was, for example haircut.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    try {
      await context.read<ApiClient>().post(
        '$_org/bookings',
        body: {
          'booked_for': _day,
          'service': service,
          'status': _status,
          'customer_id': _customer,
          'staff': _staff.text.trim().isEmpty ? null : _staff.text.trim(),
          'cancelled_on': _status == 'cancelled' ? _cancelledOn : null,
          'notes': _notes.text.trim().isEmpty ? null : _notes.text.trim(),
        },
      );
      if (!mounted) return;
      _service.clear();
      _notes.clear();
      setState(() => _saved = 'Appointment saved.');
      await _loadChoices();
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _suggest(
    TextEditingController controller,
    List<String> options,
    String label,
  ) {
    return Autocomplete<String>(
      optionsBuilder: (text) => options.where(
        (o) => o.toLowerCase().contains(text.text.trim().toLowerCase()),
      ),
      onSelected: (value) => controller.text = value,
      fieldViewBuilder: (context, field, focus, onSubmit) {
        // keep what is typed, even if nothing is picked from the list
        field.addListener(() => controller.text = field.text);
        return TextField(
          controller: field,
          focusNode: focus,
          decoration: InputDecoration(labelText: label),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Type in an appointment',
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
                  _suggest(_service, _usedServices, 'Service'),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    key: const ValueKey('appointment-day'),
                    onPressed: () async {
                      final day = await _pickDay(_day);
                      if (day != null) setState(() => _day = day);
                    },
                    icon: const Icon(Icons.calendar_today_rounded, size: 18),
                    label: Text('Day of the appointment: ${ukDate(_day)}'),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    initialValue: _status,
                    decoration: const InputDecoration(
                      labelText: 'What happened?',
                    ),
                    items: [
                      for (final (value, label) in _statuses)
                        DropdownMenuItem(value: value, child: Text(label)),
                    ],
                    onChanged: (v) => setState(() => _status = v ?? _status),
                  ),
                  if (_status == 'cancelled') ...[
                    const SizedBox(height: 12),
                    OutlinedButton.icon(
                      onPressed: () async {
                        final day = await _pickDay(_cancelledOn ?? _day);
                        if (day != null) setState(() => _cancelledOn = day);
                      },
                      icon: const Icon(Icons.event_busy_rounded, size: 18),
                      label: Text(
                        _cancelledOn == null
                            ? 'Day they cancelled (optional)'
                            : 'Cancelled on ${ukDate(_cancelledOn!)}',
                      ),
                    ),
                    Text(
                      'A cancellation the day before or on the day counts as late.',
                      style: TextStyle(color: muted, fontSize: 12),
                    ),
                  ],
                  if (_customers.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    DropdownButtonFormField<String?>(
                      initialValue: _customer,
                      decoration: const InputDecoration(
                        labelText: 'Customer (optional)',
                        helperText: 'Choose one to see who books again.',
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
                  const SizedBox(height: 12),
                  _suggest(_staff, _usedStaff, 'Who was doing it? (optional)'),
                  TextField(
                    controller: _notes,
                    maxLength: 300,
                    decoration: const InputDecoration(
                      labelText: 'Notes (optional)',
                    ),
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
                    child: Text(_busy ? 'Saving...' : 'Save appointment'),
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
