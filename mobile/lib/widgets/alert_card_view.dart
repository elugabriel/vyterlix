import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_alert_card.dart';
import '../theme.dart';
import 'charts.dart';
import 'common.dart';
import 'plain.dart';

const _effort = {
  'low': 'A little effort',
  'medium': 'A moderate amount of effort',
  'high': 'A lot of effort',
};
const _cost = {
  'none': 'Costs nothing',
  'low': 'Costs a little',
  'medium': 'Costs a moderate amount',
  'high': 'Costs a lot',
};

Color priorityColor(String level) {
  switch (level) {
    case 'high':
      return Palette.bad;
    case 'medium':
      return Palette.warn;
    default:
      return Palette.lightMuted;
  }
}

/// An alert and what to do about it, in one place: what happened in everyday words, why it
/// matters, how urgent, a small chart, what we suggest, and the buttons to decide.
/// Accepting makes a real action, linked to the change.
class AlertCardView extends StatefulWidget {
  const AlertCardView({
    super.key,
    required this.business,
    required this.alertId,
    this.onChanged,
  });

  final Business business;
  final String alertId;

  /// Called after a decision, so the screen around it can refresh.
  final VoidCallback? onChanged;

  @override
  State<AlertCardView> createState() => _AlertCardViewState();
}

class _AlertCardViewState extends State<AlertCardView> {
  AlertCard? _card;
  String? _error;
  bool _busy = false;
  String? _problem;

  String get _path =>
      '/organizations/${widget.business.id}/alerts/${widget.alertId}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get('$_path/card');
      if (mounted) {
        setState(
          () => _card = AlertCard.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _decide(String verb, Object? body) async {
    setState(() {
      _busy = true;
      _problem = null;
    });
    try {
      final data = await context.read<ApiClient>().post(
        '$_path/$verb',
        body: body ?? <String, dynamic>{},
      );
      if (!mounted) return;
      setState(() => _card = AlertCard.fromJson(data as Map<String, dynamic>));
      widget.onChanged?.call();
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _edit(CardSuggestion s) async {
    final title = TextEditingController(text: s.title);
    final description = TextEditingController(text: s.description);
    final steps = TextEditingController(text: s.steps.join('\n'));
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Change it before accepting'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: title,
                decoration: const InputDecoration(labelText: 'What to do'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: description,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'More detail'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: steps,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: 'Steps (one on each line)',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Accept with my changes'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final lines = [
      for (final line in steps.text.split('\n'))
        if (line.trim().isNotEmpty) {'text': line.trim()},
    ];
    await _decide('accept', {
      if (title.text.trim().isNotEmpty) 'title': title.text.trim(),
      if (description.text.trim().isNotEmpty)
        'description': description.text.trim(),
      'steps': lines,
    });
  }

  Future<void> _assign(CardSuggestion s) async {
    List<(String, String)> people = [];
    try {
      final data = await context.read<ApiClient>().get(
        '/organizations/${widget.business.id}/members',
      );
      people = [
        for (final m in data as List)
          if ((m as Map<String, dynamic>)['status'] == 'active')
            ('${m['user_id']}', '${m['full_name']}'),
      ];
    } on ApiException {
      // the team list is a convenience; "Me" still works
    }
    if (!mounted) return;
    String? owner;
    var due = DateTime.parse(s.dueBy);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setLocal) => AlertDialog(
          title: const Text('Who should do it?'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              DropdownButtonFormField<String?>(
                key: const ValueKey('assign-owner'),
                initialValue: owner,
                decoration: const InputDecoration(labelText: 'Assign to'),
                items: [
                  const DropdownMenuItem<String?>(
                    value: null,
                    child: Text('Me'),
                  ),
                  for (final (id, name) in people)
                    DropdownMenuItem<String?>(value: id, child: Text(name)),
                ],
                onChanged: (v) => setLocal(() => owner = v),
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                key: const ValueKey('assign-due'),
                onPressed: () async {
                  final picked = await showDatePicker(
                    context: context,
                    initialDate: due,
                    firstDate: DateTime.now(),
                    lastDate: DateTime.now().add(const Duration(days: 730)),
                  );
                  if (picked != null) setLocal(() => due = picked);
                },
                icon: const Icon(Icons.calendar_today_rounded, size: 18),
                label: Text('Due by ${ukDate(_iso(due))}'),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Accept and assign'),
            ),
          ],
        ),
      ),
    );
    if (ok != true) return;
    await _decide('accept', {
      'owner_user_id': ?owner,
      'target_date': _iso(due),
    });
  }

  String _iso(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  Future<void> _dismiss() async {
    final reason = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Dismiss this suggestion?'),
        content: TextField(
          controller: reason,
          maxLines: 2,
          decoration: const InputDecoration(
            labelText: 'Why not? (optional, kept for next time)',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Dismiss it'),
          ),
        ],
      ),
    );
    if (ok != true) return;
    final text = reason.text.trim();
    await _decide('dismiss', {'reason': text.isEmpty ? null : text});
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final card = _card;
    if (card == null) {
      return const Padding(
        padding: EdgeInsets.all(32),
        child: Center(child: CircularProgressIndicator()),
      );
    }
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Tag(
          '${card.priority.label} priority',
          color: priorityColor(card.priority.level),
        ),
        const SizedBox(height: 10),
        Text(
          card.headline,
          style: Theme.of(context).textTheme.titleLarge
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 10),
        if (card.state == 'other')
          Text(card.happened)
        else
          PlainBlock(plain: card.plain),
        if (card.numbers != null) ...[
          const SizedBox(height: 8),
          Text(card.numbers!, style: TextStyle(color: muted)),
        ],
        if (card.seasonNote != null) ...[
          const SizedBox(height: 4),
          Text(card.seasonNote!, style: TextStyle(color: muted)),
        ],
        const SizedBox(height: 8),
        Text(
          card.priority.reason,
          style: TextStyle(color: muted, fontSize: 13),
        ),
        if (card.chart != null && card.chart!.values.isNotEmpty) ...[
          const SizedBox(height: 16),
          BarChart(
            values: card.chart!.values,
            markedIndex: card.chart!.markedIndex,
            height: 110,
          ),
          const SizedBox(height: 4),
          Text(
            '${card.chart!.name} by month. The orange bar is the month this is about.',
            style: TextStyle(color: muted, fontSize: 12),
          ),
        ],
        if (card.evidence.isNotEmpty) ...[
          const SectionTitle('What we found'),
          for (final e in card.evidence)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('•  '),
                  Expanded(child: Text(e)),
                ],
              ),
            ),
        ],
        ..._state(card, muted),
        if (_problem != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Text(
              _problem!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ],
    );
  }

  List<Widget> _state(AlertCard card, Color muted) {
    switch (card.state) {
      case 'open':
        return [
          _suggestion(card.suggestion!, muted),
          if (card.canDecide)
            _decisions(card.suggestion!, muted)
          else
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                'Only an owner or manager can accept or dismiss this.',
                style: TextStyle(color: muted),
              ),
            ),
        ];
      case 'proposed':
        return [
          if (card.suggestion != null) _suggestion(card.suggestion!, muted),
          _actionBlock(
            card.action,
            'Put forward for approval. It waits for the owner to approve it.',
            muted,
          ),
        ];
      case 'accepted':
        return [
          _actionBlock(
            card.action,
            'You accepted this, so it is now an action.',
            muted,
          ),
        ];
      case 'dismissed':
        return [
          const SectionTitle('Dismissed'),
          Text(
            'You decided not to act on this. ${card.dismissedReason ?? ''}'
                .trim(),
            style: TextStyle(color: muted),
          ),
        ];
      case 'good_news':
        return const [
          SectionTitle('Good news'),
          Text(
            'This change is in your favour, so there is nothing to put right.',
          ),
        ];
      case 'no_suggestion':
        return [
          const SectionTitle('What to do'),
          Text(
            'We cannot say what to do about this yet. Open the explanation on the website to see what we found.',
            style: TextStyle(color: muted),
          ),
        ];
      case 'not_yet':
        return [
          const SectionTitle('What to do'),
          Text(
            'A suggestion has not been made yet. An owner or manager can open this alert to get one.',
            style: TextStyle(color: muted),
          ),
        ];
      default:
        return [
          if (card.nextStepText != null) ...[
            const SectionTitle('Next step'),
            Text(card.nextStepText!),
          ],
        ];
    }
  }

  Widget _suggestion(CardSuggestion s, Color muted) {
    return Card(
      margin: const EdgeInsets.only(top: 16),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'WHAT WE SUGGEST',
              style: TextStyle(
                color: muted,
                fontSize: 11,
                fontWeight: FontWeight.w700,
                letterSpacing: 1,
              ),
            ),
            const SizedBox(height: 6),
            Text(
              s.title,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
            const SizedBox(height: 6),
            Text(s.description),
            const SizedBox(height: 6),
            Text(
              '${_effort[s.effort]} · ${_cost[s.costLevel]} · usually starts to show in about ${s.daysToEffect} days',
              style: TextStyle(color: muted, fontSize: 13),
            ),
            if (s.whyThisOne.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(s.whyThisOne, style: TextStyle(color: muted, fontSize: 13)),
            ],
            if (s.steps.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('See the steps'),
                children: [
                  for (var i = 0; i < s.steps.length; i++)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Text('${i + 1}. ${s.steps[i]}'),
                      ),
                    ),
                ],
              ),
            if (s.otherOptions > 0)
              Text(
                'We also looked at ${s.otherOptions} other ${s.otherOptions == 1 ? 'way' : 'ways'}; they are on the website.',
                style: TextStyle(color: muted, fontSize: 12),
              ),
          ],
        ),
      ),
    );
  }

  Widget _decisions(CardSuggestion s, Color muted) {
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'If you accept it as it is: it is assigned to you, due by ${ukDate(s.dueBy)}, and we check the result after that.',
            style: TextStyle(color: muted, fontSize: 13),
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy ? null : () => _decide('accept', null),
            child: const Text('Accept'),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: _busy ? null : () => _edit(s),
                  child: const Text('Edit'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: _busy ? null : () => _assign(s),
                  child: const Text('Assign'),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton(
                  onPressed: _busy ? null : _dismiss,
                  child: const Text('Dismiss'),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _actionBlock(CardAction? action, String intro, Color muted) {
    if (action == null) return Text(intro);
    return Card(
      margin: const EdgeInsets.only(top: 16),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(intro),
            const SizedBox(height: 8),
            Text(
              action.title,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
            const SizedBox(height: 4),
            Text(
              'Assigned to ${action.owner ?? 'nobody yet'}${action.targetDate == null ? '' : ', due by ${ukDate(action.targetDate!)}'}.',
            ),
            if (action.checkFrom != null)
              Text(
                'We will check how it went from ${ukDate(action.checkFrom!)}.',
                style: TextStyle(color: muted, fontSize: 13),
              ),
            Text(
              'Find it under Actions.',
              style: TextStyle(color: muted, fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }
}
