import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_actions.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'action_detail_screen.dart';
import 'recommendation_screen.dart';

/// The colour that goes with an action's state.
Color actionColor(String status) {
  switch (status) {
    case 'overdue':
      return Palette.bad;
    case 'pending':
      return Palette.warn;
    case 'completed':
      return Palette.ok;
    case 'in_progress':
    case 'partially_completed':
      return Palette.accent;
    default:
      return Palette.lightMuted;
  }
}

const _filters = [
  ('open', 'Still to do'),
  ('mine', 'Mine'),
  ('pending', 'Waiting for approval'),
  ('completed', 'Done'),
  ('ideas', 'Ideas'),
];

/// The work decided on, who is doing it and by when, and Vyterlix's ideas for what to do next.
class ActionsScreen extends StatefulWidget {
  const ActionsScreen({super.key, required this.business});

  final Business business;

  @override
  State<ActionsScreen> createState() => ActionsScreenState();
}

class ActionsScreenState extends State<ActionsScreen> {
  String _filter = 'open';
  List<ActionSummary>? _actions;
  List<RecommendationSummary>? _ideas;
  ActionCounts? _counts;
  String? _error;

  String get _base => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  /// Show a particular list (for example the ideas, from the Today screen).
  void showFilter(String filter) {
    setState(() {
      _filter = filter;
      _actions = null;
      _ideas = null;
    });
    _load();
  }

  Map<String, String> _query() {
    switch (_filter) {
      case 'mine':
        return {'mine': 'true', 'open_only': 'true'};
      case 'pending':
        return {'status': 'pending'};
      case 'completed':
        return {'status': 'completed'};
      default:
        return {'open_only': 'true'};
    }
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final counts = api.get('$_base/actions/summary');
      if (_filter == 'ideas') {
        final data = await api.get(
          '$_base/recommendations',
          query: {'status': 'open'},
        );
        final summary = await counts;
        if (!mounted) return;
        setState(() {
          _ideas = [
            for (final item in data as List)
              RecommendationSummary.fromJson(item as Map<String, dynamic>),
          ];
          _counts = ActionCounts.fromJson(summary as Map<String, dynamic>);
        });
      } else {
        final data = await api.get('$_base/actions', query: _query());
        final summary = await counts;
        if (!mounted) return;
        setState(() {
          _actions = [
            for (final item in data as List)
              ActionSummary.fromJson(item as Map<String, dynamic>),
          ];
          _counts = ActionCounts.fromJson(summary as Map<String, dynamic>);
        });
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _openAction(ActionSummary action) async {
    await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            ActionDetailScreen(business: widget.business, actionId: action.id),
      ),
    );
    if (mounted) await _load();
  }

  Future<void> _openIdea(RecommendationSummary idea) async {
    await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => RecommendationScreen(
          business: widget.business,
          eventId: idea.eventId,
        ),
      ),
    );
    if (mounted) await _load();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final ready = _filter == 'ideas' ? _ideas != null : _actions != null;
    final counts = _counts;
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (counts != null)
            Text(
              _headline(counts),
              style: Theme.of(context).textTheme.titleMedium
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final (key, label) in _filters) ...[
                  ChoiceChip(
                    label: Text(label),
                    selected: _filter == key,
                    showCheckmark: false,
                    onSelected: (_) => showFilter(key),
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
          if (!ready)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (_filter == 'ideas') ...[
            if (_ideas!.isEmpty)
              _empty(
                'No ideas right now. They appear when a figure moves.',
                muted,
              ),
            for (final idea in _ideas!) ...[
              _IdeaCard(idea: idea, onTap: () => _openIdea(idea)),
              const SizedBox(height: 12),
            ],
          ] else ...[
            if (_actions!.isEmpty) _empty(_emptyText(), muted),
            for (final action in _actions!) ...[
              _ActionCard(action: action, onTap: () => _openAction(action)),
              const SizedBox(height: 12),
            ],
          ],
        ],
      ),
    );
  }

  Widget _empty(String text, Color muted) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 32),
    child: Center(
      child: Text(
        text,
        textAlign: TextAlign.center,
        style: TextStyle(color: muted),
      ),
    ),
  );

  String _emptyText() {
    switch (_filter) {
      case 'mine':
        return 'Nothing is assigned to you.';
      case 'pending':
        return 'Nothing is waiting for approval.';
      case 'completed':
        return 'Nothing has been finished yet.';
      default:
        return 'No actions here. Take up an idea to start one.';
    }
  }

  String _headline(ActionCounts c) {
    final parts = <String>['${c.open} still to do'];
    if (c.overdue > 0) parts.add('${c.overdue} late');
    if (c.dueSoon > 0) parts.add('${c.dueSoon} due soon');
    return '${parts.join(', ')}.';
  }
}

class _ActionCard extends StatelessWidget {
  const _ActionCard({required this.action, required this.onTap});

  final ActionSummary action;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = actionColor(action.status);
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final when = action.targetDate == null
        ? null
        : action.daysLate > 0
        ? '${action.daysLate} day${action.daysLate == 1 ? '' : 's'} late (was due ${ukDate(action.targetDate!)})'
        : 'Due ${ukDate(action.targetDate!)}';
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Tag(action.statusLabel, color: color),
                  Text(
                    action.kpiName,
                    style: TextStyle(color: muted, fontSize: 13),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                action.title,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              Text(
                [
                  action.owner == null
                      ? 'Not given to anyone yet'
                      : action.owner!.name,
                  ?when,
                ].join(' · '),
                style: TextStyle(color: muted, fontSize: 13),
              ),
              if (action.progress.total > 0) ...[
                const SizedBox(height: 12),
                ClipRRect(
                  borderRadius: BorderRadius.circular(999),
                  child: LinearProgressIndicator(
                    value: action.progress.percent / 100,
                    minHeight: 6,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  '${action.progress.done} of ${action.progress.total} steps',
                  style: TextStyle(color: muted, fontSize: 12),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _IdeaCard extends StatelessWidget {
  const _IdeaCard({required this.idea, required this.onTap});

  final RecommendationSummary idea;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Tag('Idea', color: Palette.accent),
                  Text(
                    '${idea.kpiName} · ${monthName(idea.periodStart)}',
                    style: TextStyle(color: muted, fontSize: 13),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                idea.headline,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              if (idea.recommended != null) ...[
                const SizedBox(height: 4),
                Text(
                  'Suggested: ${idea.recommended}',
                  style: TextStyle(color: muted),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
