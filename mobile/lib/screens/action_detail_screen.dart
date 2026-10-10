import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_actions.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'actions_screen.dart' show actionColor;

const _moveText = {
  'accepted': 'Approve and accept',
  'in_progress': 'Start it',
  'partially_completed': 'Partly done',
  'completed': 'Mark as done',
  'cancelled': 'Cancel it',
};

const _kindText = {
  'created': 'Started',
  'approved': 'Approved',
  'rejected': 'Turned down',
  'status': 'Status',
  'note': 'Note',
  'assignment': 'Given to',
  'dates': 'Dates',
  'modification': 'Changed',
  'steps': 'Steps',
  'evidence': 'Evidence',
  'outcome': 'Result',
  'overdue': 'Overdue',
};

/// One action in full: what was decided, the steps, where it has got to, and what came of it.
class ActionDetailScreen extends StatefulWidget {
  const ActionDetailScreen({
    super.key,
    required this.business,
    required this.actionId,
  });

  final Business business;
  final String actionId;

  @override
  State<ActionDetailScreen> createState() => _ActionDetailScreenState();
}

class _ActionDetailScreenState extends State<ActionDetailScreen> {
  ActionDetail? _detail;
  String? _error;
  bool _busy = false;
  final _note = TextEditingController();

  String get _path =>
      '/organizations/${widget.business.id}/actions/${widget.actionId}';

  /// A viewer can look but not change anything (the server would refuse anyway).
  bool get _canWork => widget.business.role != 'viewer';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(_path);
      if (mounted) {
        setState(
          () => _detail = ActionDetail.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  /// Send a change and show the action as it is afterwards (or say why not).
  Future<void> _send(
    Future<dynamic> Function(ApiClient api) call, {
    bool clearNote = false,
  }) async {
    setState(() => _busy = true);
    final api = context.read<ApiClient>();
    try {
      final data = await call(api);
      if (!mounted) return;
      if (clearNote) _note.clear();
      setState(
        () => _detail = ActionDetail.fromJson(data as Map<String, dynamic>),
      );
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _tick(int index, bool done) {
    final steps = [
      for (var i = 0; i < _detail!.steps.length; i++)
        (i == index
                ? ActionStep(text: _detail!.steps[i].text, done: done)
                : _detail!.steps[i])
            .toJson(),
    ];
    return _send((api) => api.patch(_path, body: {'steps': steps}));
  }

  Future<void> _move(String status) {
    final note = _note.text.trim();
    return _send(
      (api) => api.post(
        '$_path/status',
        body: {'status': status, if (note.isNotEmpty) 'note': note},
      ),
      clearNote: true,
    );
  }

  Future<void> _addNote() {
    final note = _note.text.trim();
    if (note.isEmpty) return Future.value();
    return _send(
      (api) => api.post('$_path/notes', body: {'note': note}),
      clearNote: true,
    );
  }

  Future<void> _approve() => _send((api) => api.post('$_path/approve'));

  Future<void> _reject() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => const _ReasonDialog(),
    );
    if (reason == null) return;
    await _send(
      (api) => api.post(
        '$_path/reject',
        body: reason.isEmpty ? null : {'reason': reason},
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Action',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final detail = _detail;
    if (detail == null) return const Center(child: CircularProgressIndicator());
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final color = actionColor(detail.status);
    final finished =
        detail.status == 'completed' || detail.status == 'cancelled';
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Wrap(
          spacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Tag(detail.statusLabel, color: color),
            if (detail.daysLate > 0)
              Tag(
                '${detail.daysLate} day${detail.daysLate == 1 ? '' : 's'} late',
                color: Palette.bad,
              ),
          ],
        ),
        const SizedBox(height: 12),
        Text(
          detail.title,
          style: Theme.of(context).textTheme.titleLarge
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        if (detail.description.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(detail.description),
        ],
        const SizedBox(height: 16),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _row(
                  'Being done by',
                  detail.owner?.name ?? 'Nobody yet',
                  muted,
                ),
                _row(
                  'Started',
                  detail.startDate == null ? '–' : ukDate(detail.startDate!),
                  muted,
                ),
                _row(
                  'Due',
                  detail.targetDate == null ? '–' : ukDate(detail.targetDate!),
                  muted,
                ),
              ],
            ),
          ),
        ),
        if (detail.steps.isNotEmpty) ...[
          const SectionTitle('Steps'),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: detail.progress.percent / 100,
              minHeight: 8,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            '${detail.progress.done} of ${detail.progress.total} done',
            style: TextStyle(color: muted, fontSize: 13),
          ),
          const SizedBox(height: 8),
          for (var i = 0; i < detail.steps.length; i++)
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              controlAffinity: ListTileControlAffinity.leading,
              value: detail.steps[i].done,
              title: Text(detail.steps[i].text),
              onChanged: (_canWork && !finished && !_busy)
                  ? (value) => _tick(i, value ?? false)
                  : null,
            ),
        ],
        const SectionTitle('What was decided'),
        _DecisionCard(decision: detail.decision),
        if (detail.followUp != null || detail.outcome != null) ...[
          const SectionTitle('How it went'),
          _ResultCard(followUp: detail.followUp, outcome: detail.outcome),
        ],
        if (_canWork && !finished) ...[
          const SectionTitle('What do you want to do?'),
          TextField(
            controller: _note,
            maxLines: 2,
            maxLength: 500,
            decoration: const InputDecoration(labelText: 'A note (optional)'),
          ),
          const SizedBox(height: 8),
          if (detail.status == 'pending') ...[
            FilledButton(
              onPressed: _busy ? null : _approve,
              child: const Text('Approve and accept'),
            ),
            const SizedBox(height: 10),
            OutlinedButton(
              onPressed: _busy ? null : _reject,
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(50),
              ),
              child: const Text('Turn it down'),
            ),
          ] else ...[
            for (final next in detail.nextStatuses) ...[
              next == detail.nextStatuses.first
                  ? FilledButton(
                      onPressed: _busy ? null : () => _move(next),
                      child: Text(_moveText[next] ?? next),
                    )
                  : OutlinedButton(
                      onPressed: _busy ? null : () => _move(next),
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size.fromHeight(50),
                      ),
                      child: Text(_moveText[next] ?? next),
                    ),
              const SizedBox(height: 10),
            ],
          ],
          TextButton(
            onPressed: _busy ? null : _addNote,
            child: const Text('Just add the note'),
          ),
        ],
        if (!_canWork && !finished)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              'Only owners and managers can change an action.',
              style: TextStyle(color: muted),
            ),
          ),
        const SectionTitle('What has happened'),
        for (final update in detail.updates.reversed)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Padding(
                  padding: EdgeInsets.only(top: 6, right: 12),
                  child: Icon(Icons.circle, size: 8, color: Palette.accent),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${_kindText[update.kind] ?? update.kind} · ${update.user?.name ?? 'Vyterlix'}',
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      Text(
                        ukDateTime(update.createdAt),
                        style: TextStyle(color: muted, fontSize: 12),
                      ),
                      if (update.note != null) Text(update.note!),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _row(String label, String value, Color muted) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(
      children: [
        Expanded(
          child: Text(label, style: TextStyle(color: muted)),
        ),
        Text(value, style: const TextStyle(fontWeight: FontWeight.w600)),
      ],
    ),
  );
}

class _DecisionCard extends StatelessWidget {
  const _DecisionCard({required this.decision});

  final Decision decision;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Aimed at ${decision.kpiName}',
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            if (decision.baselineValue != null &&
                decision.baselinePeriod != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'It was ${formatValue(decision.baselineValue, decision.unit)} in ${monthName(decision.baselinePeriod!)}.',
                ),
              ),
            if (decision.expectedImpactValue != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'We expected it to improve by about ${formatValue(decision.expectedImpactValue, decision.unit)}.',
                ),
              ),
            if (decision.target != null)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('About: ${decision.target}'),
              ),
            const SizedBox(height: 8),
            Text(
              'Taken up ${ukDate(decision.acceptedAt)}${decision.acceptedBy == null ? '' : ' by ${decision.acceptedBy!.name}'}.',
              style: TextStyle(color: muted, fontSize: 12),
            ),
          ],
        ),
      ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.followUp, required this.outcome});

  final FollowUp? followUp;
  final Outcome? outcome;

  @override
  Widget build(BuildContext context) {
    final result = outcome;
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: result == null
            ? Text(
                followUp!.isDue
                    ? 'It is time to check how this went. This happens by itself; you will be told.'
                    : 'We will check how this went on ${ukDate(followUp!.dueDate)}, using ${monthName(followUp!.measureMonth)}.',
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Tag(
                    result.label,
                    color: result.outcome == 'successful'
                        ? Palette.ok
                        : result.outcome == 'unsuccessful'
                        ? Palette.bad
                        : Palette.warn,
                  ),
                  const SizedBox(height: 8),
                  Text(result.reason),
                  if (result.baselineValue != null &&
                      result.measuredValue != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        '${result.kpiName}: ${formatValue(result.baselineValue, result.unit)} before, ${formatValue(result.measuredValue, result.unit)} after.',
                        style: TextStyle(color: muted),
                      ),
                    ),
                  if (result.achievedPct != null)
                    Text(
                      '${result.achievedPct}% of what was expected.',
                      style: TextStyle(color: muted),
                    ),
                  if (result.alternative != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text('Another idea: ${result.alternative}'),
                    ),
                ],
              ),
      ),
    );
  }
}

class _ReasonDialog extends StatefulWidget {
  const _ReasonDialog();

  @override
  State<_ReasonDialog> createState() => _ReasonDialogState();
}

class _ReasonDialogState extends State<_ReasonDialog> {
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Turn this down?'),
      content: TextField(
        controller: _reason,
        maxLines: 2,
        maxLength: 300,
        decoration: const InputDecoration(labelText: 'Why? (optional)'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Keep it'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_reason.text.trim()),
          style: FilledButton.styleFrom(minimumSize: const Size(120, 44)),
          child: const Text('Turn it down'),
        ),
      ],
    );
  }
}
