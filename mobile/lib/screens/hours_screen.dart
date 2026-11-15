import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';

String _isoDay(DateTime d) =>
    '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

/// Type in the hours someone worked on a day (the quick version of the website's Hours worked tab).
/// It lets Vyterlix show sales for each hour worked and how much of the team's time went on
/// appointments.
class HoursScreen extends StatefulWidget {
  const HoursScreen({super.key, required this.business});

  final Business business;

  @override
  State<HoursScreen> createState() => _HoursScreenState();
}

class _HoursScreenState extends State<HoursScreen> {
  final _staff = TextEditingController();
  final _hours = TextEditingController();
  String _day = _isoDay(DateTime.now());
  List<String> _usedStaff = [];
  bool _busy = false;
  String? _error;
  String? _saved;

  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _loadStaff();
  }

  @override
  void dispose() {
    _staff.dispose();
    _hours.dispose();
    super.dispose();
  }

  /// Names already used on appointments, to pick from. The form works without them.
  Future<void> _loadStaff() async {
    try {
      final data = await context.read<ApiClient>().get(
        '$_org/bookings/choices',
      ) as Map<String, dynamic>;
      if (mounted) {
        setState(
          () => _usedStaff = [for (final s in data['staff'] as List) '$s'],
        );
      }
    } on ApiException {
      // the form still works
    }
  }

  Future<void> _save() async {
    final staff = _staff.text.trim();
    final hours = double.tryParse(_hours.text.trim().replaceAll(',', '.'));
    if (staff.isEmpty) {
      setState(() => _error = 'Say who worked.');
      return;
    }
    if (hours == null || hours <= 0 || hours > 24) {
      setState(() => _error = 'Hours must be more than 0 and no more than 24.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
      _saved = null;
    });
    try {
      await context.read<ApiClient>().post(
        '$_org/staff-hours',
        body: {'worked_on': _day, 'staff': staff, 'hours': '$hours'},
      );
      if (!mounted) return;
      _hours.clear();
      setState(() => _saved = 'Hours saved.');
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
          'Type in hours worked',
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
                  OutlinedButton.icon(
                    key: const ValueKey('hours-day'),
                    onPressed: () async {
                      final picked = await showDatePicker(
                        context: context,
                        initialDate: DateTime.parse(_day),
                        firstDate: DateTime(2000),
                        lastDate: DateTime.now(),
                      );
                      if (picked != null) {
                        setState(() => _day = _isoDay(picked));
                      }
                    },
                    icon: const Icon(Icons.calendar_today_rounded, size: 18),
                    label: Text('Day worked: ${ukDate(_day)}'),
                  ),
                  const SizedBox(height: 12),
                  Autocomplete<String>(
                    optionsBuilder: (text) => _usedStaff.where(
                      (o) => o.toLowerCase().contains(
                        text.text.trim().toLowerCase(),
                      ),
                    ),
                    onSelected: (value) => _staff.text = value,
                    fieldViewBuilder: (context, field, focus, onSubmit) {
                      field.addListener(() => _staff.text = field.text);
                      return TextField(
                        controller: field,
                        focusNode: focus,
                        decoration: const InputDecoration(
                          labelText: 'Who worked? (a name)',
                          helperText:
                              'Use the same name you use on appointments.',
                        ),
                      );
                    },
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _hours,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Hours worked',
                      helperText: 'For example: 7.5',
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'These hours let Vyterlix show your sales for each hour worked, and how much of your team\'s time went on appointments.',
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
                    child: Text(_busy ? 'Saving...' : 'Save hours'),
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
