import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_memory.dart';
import '../theme.dart';
import '../widgets/common.dart';

const _costChoices = [
  (null, 'No limit'),
  ('none', 'Nothing'),
  ('low', 'A little'),
  ('medium', 'A moderate amount'),
];
const _effortChoices = [
  (null, 'No limit'),
  ('low', 'A little effort'),
  ('medium', 'A moderate amount of effort'),
];

Color outcomeColor(String outcome) {
  switch (outcome) {
    case 'successful':
      return Palette.ok;
    case 'partially_successful':
      return Palette.warn;
    case 'unsuccessful':
      return Palette.bad;
    default:
      return Palette.lightMuted;
  }
}

/// What Vyterlix has learned about the business, and the limits on what it may suggest.
class MemoryScreen extends StatefulWidget {
  const MemoryScreen({super.key, required this.business});

  final Business business;

  @override
  State<MemoryScreen> createState() => _MemoryScreenState();
}

class _MemoryScreenState extends State<MemoryScreen> {
  Memory? _memory;
  List<(String, String)> _library = [];
  String? _error;
  String? _problem;
  String? _note;
  bool _busy = false;

  String? _cost;
  String? _effort;
  bool _quick = false;
  Set<String> _never = {};

  bool get _isOwner => widget.business.role == 'owner';
  bool get _canRefresh => widget.business.role != 'viewer';
  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final memory = Memory.fromJson(
        await api.get('$_org/memory') as Map<String, dynamic>,
      );
      List<(String, String)> library = _library;
      try {
        library = [
          for (final a in await api.get('$_org/interventions') as List)
            ('${(a as Map)['code']}', '${a['name']}'),
        ];
      } on ApiException {
        // the list of actions is only needed for the limits
      }
      if (!mounted) return;
      setState(() {
        _memory = memory;
        _library = library;
        _cost = memory.limits.maxCostLevel;
        _effort = memory.limits.maxEffort;
        _quick = memory.limits.quickResultsOnly;
        _never = {...memory.limits.excluded};
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _act(
    Future<void> Function(ApiClient api) action,
    String done,
  ) async {
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      await action(context.read<ApiClient>());
      if (mounted) setState(() => _note = done);
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _refresh() => _act((api) async {
    await api.post('$_org/memory/rebuild');
    await _load();
  }, 'Refreshed.');

  Future<void> _saveLimits() => _act((api) async {
    await api.put(
      '$_org/memory/constraints',
      body: {
        'max_cost_level': _cost,
        'max_effort': _effort,
        'excluded_actions': _never.toList()..sort(),
        'quick_results_only': _quick,
      },
    );
  }, 'Your limits are saved.');

  Widget _list(String title, List<String> items, String empty) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SectionTitle(title),
        if (items.isEmpty)
          Text(empty, style: TextStyle(color: muted))
        else
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final i in items)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(i),
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final m = _memory;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'What we know',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : m == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  Text(
                    'This is what Vyterlix has learned about your business from your own records and from what you have tried. It is used to rank the next suggestion, and nothing here is shared with anyone else.',
                    style: TextStyle(color: muted),
                  ),
                  if (_canRefresh) ...[
                    const SizedBox(height: 12),
                    OutlinedButton(
                      onPressed: _busy ? null : _refresh,
                      child: const Text('Refresh what we know'),
                    ),
                  ],
                  if (_problem != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: Text(
                        _problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  if (_note != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 12),
                      child: Text(_note!),
                    ),
                  _list(
                    'What is normal for you',
                    m.normalRanges,
                    'Not enough months of figures yet. Half a year is needed before we can say what is normal.',
                  ),
                  _list(
                    'Patterns in your customers',
                    m.customerPatterns,
                    'Nothing to say yet. Bring in your sales and customers first.',
                  ),
                  _list('Your goals', m.goals, 'You have not set any goals.'),
                  _list(
                    'Your busy and quiet times',
                    m.seasons,
                    'No seasons set.',
                  ),
                  _limits(muted),
                  _learned(m, muted),
                  const SectionTitle(
                    'What we remembered when we last made a suggestion',
                  ),
                  if (m.recentUse.isEmpty)
                    Text(
                      'Nothing yet. Your limits and what you have learned are used, and listed here, whenever a suggestion is made.',
                      style: TextStyle(color: muted),
                    ),
                  for (final r in m.recentUse)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              ukDateTime(r.createdAt),
                              style: TextStyle(color: muted, fontSize: 13),
                            ),
                            for (final u in r.used)
                              Padding(
                                padding: const EdgeInsets.only(top: 6),
                                child: Text(u),
                              ),
                          ],
                        ),
                      ),
                    ),
                  if (m.lastRebuilt != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 16),
                      child: Text(
                        'What we know about your figures was last worked out on ${ukDateTime(m.lastRebuilt!)}.',
                        style: TextStyle(color: muted, fontSize: 13),
                      ),
                    ),
                ],
              ),
            ),
    );
  }

  Widget _limits(Color muted) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('Your limits'),
        Text(
          'Suggestions that break these are left out, and the suggestion says what was left out and why.',
          style: TextStyle(color: muted),
        ),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                DropdownButtonFormField<String?>(
                  key: const ValueKey('limit-cost'),
                  initialValue: _cost,
                  decoration: const InputDecoration(
                    labelText: 'The most an action may cost',
                  ),
                  items: [
                    for (final (v, label) in _costChoices)
                      DropdownMenuItem(value: v, child: Text(label)),
                  ],
                  onChanged: _isOwner ? (v) => setState(() => _cost = v) : null,
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String?>(
                  key: const ValueKey('limit-effort'),
                  initialValue: _effort,
                  decoration: const InputDecoration(
                    labelText: 'The most effort you can give',
                  ),
                  items: [
                    for (final (v, label) in _effortChoices)
                      DropdownMenuItem(value: v, child: Text(label)),
                  ],
                  onChanged: _isOwner
                      ? (v) => setState(() => _effort = v)
                      : null,
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Only suggest actions that show within a month',
                  ),
                  value: _quick,
                  onChanged: _isOwner
                      ? (v) => setState(() => _quick = v)
                      : null,
                ),
                if (_library.isNotEmpty) ...[
                  const Text(
                    'Never suggest these',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                  for (final (code, name) in _library)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      controlAffinity: ListTileControlAffinity.leading,
                      title: Text(name),
                      value: _never.contains(code),
                      onChanged: _isOwner
                          ? (v) => setState(() {
                              v == true
                                  ? _never.add(code)
                                  : _never.remove(code);
                            })
                          : null,
                    ),
                ],
                if (_isOwner)
                  FilledButton(
                    onPressed: _busy ? null : _saveLimits,
                    child: const Text('Save my limits'),
                  )
                else
                  Text(
                    'Only the owner can change these limits.',
                    style: TextStyle(color: muted),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _learned(Memory m, Color muted) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('What we have learned from what you tried'),
        if (m.lessons.isEmpty)
          Text(
            'Nothing yet. When an action you took has been checked, what happened is kept here.',
            style: TextStyle(color: muted),
          ),
        for (final l in m.lessons)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Tag(l.outcomeText, color: outcomeColor(l.outcome)),
                  const SizedBox(height: 8),
                  Text(l.lesson),
                ],
              ),
            ),
          ),
        for (final p in m.patterns)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${p.action}: ${p.kpiName}',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 4),
                  Text(p.summary, style: TextStyle(color: muted, fontSize: 13)),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
