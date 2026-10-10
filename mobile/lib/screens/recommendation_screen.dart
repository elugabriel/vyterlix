import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_actions.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'action_detail_screen.dart';

const _effortText = {
  'low': 'Low effort',
  'medium': 'Some effort',
  'high': 'A lot of effort',
};
const _costText = {
  'none': 'Costs nothing',
  'low': 'Low cost',
  'medium': 'Some cost',
  'high': 'High cost',
};

/// What Vyterlix suggests doing about a change, the options it weighed, and why it chose one.
/// An owner or a manager can take one up (it becomes an action) or leave it for now.
class RecommendationScreen extends StatefulWidget {
  const RecommendationScreen({
    super.key,
    required this.business,
    required this.eventId,
  });

  final Business business;
  final String eventId;

  @override
  State<RecommendationScreen> createState() => _RecommendationScreenState();
}

class _RecommendationScreenState extends State<RecommendationScreen> {
  RecommendationDetail? _detail;
  String? _error;
  bool _busy = false;

  String get _path =>
      '/organizations/${widget.business.id}/changes/${widget.eventId}/recommendation';

  bool get _canDecide => widget.business.role != 'viewer';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(_path);
      if (mounted) {
        setState(
          () => _detail = RecommendationDetail.fromJson(
            data as Map<String, dynamic>,
          ),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _takeUp(RecommendationOption option) async {
    setState(() => _busy = true);
    final api = context.read<ApiClient>();
    try {
      final data = await api.post(
        '$_path/accept',
        body: {'option_id': option.id},
      );
      if (!mounted) return;
      final action = ActionDetail.fromJson(data as Map<String, dynamic>);
      await Navigator.of(context).pushReplacement(
        MaterialPageRoute<void>(
          builder: (_) => ActionDetailScreen(
            business: widget.business,
            actionId: action.id,
          ),
        ),
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

  Future<void> _leave() async {
    final reason = await showDialog<String>(
      context: context,
      builder: (_) => const _LeaveDialog(),
    );
    if (reason == null || !mounted) return;
    setState(() => _busy = true);
    final api = context.read<ApiClient>();
    final navigator = Navigator.of(context);
    try {
      await api.post(
        '$_path/dismiss',
        body: reason.isEmpty ? null : {'reason': reason},
      );
      navigator.pop();
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
          'Idea',
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
    final open = detail.status == 'open';
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          detail.headline,
          style: Theme.of(context).textTheme.titleLarge
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        Text(detail.rationale, style: TextStyle(color: muted)),
        if (detail.options.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              detail.status == 'accepted'
                  ? 'This has been taken up.'
                  : 'There is nothing to do about this one.',
            ),
          ),
        const SectionTitle('The options'),
        for (final option in detail.options) ...[
          _OptionCard(
            option: option,
            canTakeUp: _canDecide && open,
            busy: _busy,
            onTakeUp: () => _takeUp(option),
          ),
          const SizedBox(height: 12),
        ],
        if (_canDecide && open && detail.options.isNotEmpty)
          OutlinedButton(
            onPressed: _busy ? null : _leave,
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(50),
            ),
            child: const Text('Not now'),
          ),
        if (!_canDecide)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              'Only owners and managers can take an idea up.',
              style: TextStyle(color: muted),
            ),
          ),
        if (_canDecide && !open && detail.options.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              detail.status == 'accepted'
                  ? 'This has been taken up.'
                  : 'This has been dealt with.',
              style: TextStyle(color: muted),
            ),
          ),
      ],
    );
  }
}

class _OptionCard extends StatelessWidget {
  const _OptionCard({
    required this.option,
    required this.canTakeUp,
    required this.busy,
    required this.onTakeUp,
  });

  final RecommendationOption option;
  final bool canTakeUp;
  final bool busy;
  final VoidCallback onTakeUp;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Card(
      shape: option.isRecommended
          ? RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Palette.accent, width: 2),
            )
          : null,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 8,
              runSpacing: 4,
              children: [
                if (option.isRecommended)
                  const Tag('Recommended', color: Palette.accent),
                Tag(_effortText[option.effort] ?? option.effort),
                Tag(_costText[option.costLevel] ?? option.costLevel),
                Tag('Shows in about ${option.daysToEffect} days'),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              option.title,
              style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
            ),
            if (option.target != null)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(
                  'About: ${option.target}',
                  style: TextStyle(color: muted),
                ),
              ),
            const SizedBox(height: 6),
            Text(option.description),
            const SizedBox(height: 8),
            Text(
              'Could win back about ${formatValue(option.impactValue, option.impactUnit)}. Score ${option.totalScore} out of 100.',
              style: TextStyle(color: muted),
            ),
            if (option.steps.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('How:', style: TextStyle(color: muted)),
              for (final step in option.steps)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text('• $step'),
                ),
            ],
            if (canTakeUp) ...[
              const SizedBox(height: 12),
              option.isRecommended
                  ? FilledButton(
                      onPressed: busy ? null : onTakeUp,
                      child: const Text('Take this one up'),
                    )
                  : OutlinedButton(
                      onPressed: busy ? null : onTakeUp,
                      style: OutlinedButton.styleFrom(
                        minimumSize: const Size.fromHeight(50),
                      ),
                      child: const Text('Take this one up'),
                    ),
            ],
          ],
        ),
      ),
    );
  }
}

class _LeaveDialog extends StatefulWidget {
  const _LeaveDialog();

  @override
  State<_LeaveDialog> createState() => _LeaveDialogState();
}

class _LeaveDialogState extends State<_LeaveDialog> {
  final _reason = TextEditingController();

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Leave this idea?'),
      content: TextField(
        controller: _reason,
        maxLines: 2,
        maxLength: 300,
        decoration: const InputDecoration(labelText: 'Why not? (optional)'),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('Keep it'),
        ),
        FilledButton(
          onPressed: () => Navigator.of(context).pop(_reason.text.trim()),
          style: FilledButton.styleFrom(minimumSize: const Size(120, 44)),
          child: const Text('Leave it'),
        ),
      ],
    );
  }
}
