import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_insights.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'recommendation_screen.dart';

const _effectText = {
  'good': 'Good news',
  'bad': 'Bad news',
  'neutral': 'Worth knowing',
};
const _evidenceText = {
  'fact': 'Fact',
  'statistical': 'Worked out',
  'ai_interpretation': 'Interpretation',
  'insufficient': 'Not enough to say',
};

Color _effectColor(String effect) {
  switch (effect) {
    case 'good':
      return Palette.ok;
    case 'bad':
      return Palette.bad;
    default:
      return Palette.lightMuted;
  }
}

/// The figures that moved by more than normal, and where each one came from.
class ChangesScreen extends StatefulWidget {
  const ChangesScreen({super.key, required this.business});

  final Business business;

  @override
  State<ChangesScreen> createState() => _ChangesScreenState();
}

class _ChangesScreenState extends State<ChangesScreen> {
  String _filter = 'all';
  List<Change>? _changes;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/changes';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(
        _base,
        query: {'limit': '100', if (_filter != 'all') 'effect': _filter},
      );
      if (mounted) {
        setState(
          () => _changes = [
            for (final c in data as List)
              Change.fromJson(c as Map<String, dynamic>),
          ],
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  void _show(String filter) {
    setState(() {
      _filter = filter;
      _changes = null;
    });
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'What changed',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final changes = _changes;
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            'The figures that moved by more than normal, newest month first.',
            style: TextStyle(color: muted),
          ),
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                for (final (key, label) in const [
                  ('all', 'Everything'),
                  ('bad', 'Bad news'),
                  ('good', 'Good news'),
                ]) ...[
                  ChoiceChip(
                    label: Text(label),
                    selected: _filter == key,
                    showCheckmark: false,
                    onSelected: (_) => _show(key),
                  ),
                  const SizedBox(width: 8),
                ],
              ],
            ),
          ),
          const SizedBox(height: 16),
          if (changes == null)
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 40),
              child: Center(child: CircularProgressIndicator()),
            )
          else if (changes.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 32),
              child: Center(
                child: Text(
                  'Nothing has moved by more than normal.',
                  style: TextStyle(color: muted),
                ),
              ),
            )
          else
            for (final change in changes) ...[
              _ChangeCard(
                change: change,
                onTap: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => ChangeDetailScreen(
                      business: widget.business,
                      change: change,
                    ),
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],
        ],
      ),
    );
  }
}

class _ChangeCard extends StatelessWidget {
  const _ChangeCard({required this.change, required this.onTap});

  final Change change;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final color = _effectColor(change.effect);
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: IntrinsicHeight(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Container(width: 4, color: color),
              Expanded(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Wrap(
                        spacing: 8,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        children: [
                          Tag(
                            _effectText[change.effect] ?? change.effect,
                            color: color,
                          ),
                          if (change.severity == 'major')
                            const Tag('Big change'),
                          Text(
                            '${change.kpiName} · ${monthName(change.periodStart)}',
                            style: TextStyle(color: muted, fontSize: 13),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        change.summary,
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      if (change.explainedBySeason)
                        Padding(
                          padding: const EdgeInsets.only(top: 4),
                          child: Text(
                            'About what your busy and quiet seasons lead you to expect.',
                            style: TextStyle(color: muted, fontSize: 12),
                          ),
                        ),
                    ],
                  ),
                ),
              ),
              const Padding(
                padding: EdgeInsets.only(right: 8),
                child: Icon(Icons.chevron_right_rounded),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// One change: what moved, why it happened (with the evidence and how sure we are), and what to do.
class ChangeDetailScreen extends StatefulWidget {
  const ChangeDetailScreen({
    super.key,
    required this.business,
    required this.change,
  });

  final Business business;
  final Change change;

  @override
  State<ChangeDetailScreen> createState() => _ChangeDetailScreenState();
}

class _ChangeDetailScreenState extends State<ChangeDetailScreen> {
  Diagnosis? _diagnosis;
  bool _loaded = false;
  bool _busy = false;
  String? _error;

  String get _path =>
      '/organizations/${widget.business.id}/changes/${widget.change.id}';

  bool get _canWork => widget.business.role != 'viewer';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get('$_path/diagnosis');
      if (mounted) {
        setState(() {
          _diagnosis = Diagnosis.fromJson(data as Map<String, dynamic>);
          _loaded = true;
        });
      }
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.status == 404) {
        setState(() {
          _diagnosis = null;
          _loaded = true; // not explained yet: that is a state, not a failure
        });
      } else {
        setState(() => _error = error.message);
      }
    }
  }

  Future<void> _explain() async {
    setState(() => _busy = true);
    try {
      final data = await context.read<ApiClient>().post('$_path/diagnosis');
      if (mounted) {
        setState(
          () => _diagnosis = Diagnosis.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _toIdea() {
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => RecommendationScreen(
          business: widget.business,
          eventId: widget.change.id,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'A change',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    if (!_loaded) return const Center(child: CircularProgressIndicator());
    final change = widget.change;
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final diagnosis = _diagnosis;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Wrap(
          spacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Tag(
              _effectText[change.effect] ?? change.effect,
              color: _effectColor(change.effect),
            ),
            if (change.severity == 'major') const Tag('Big change'),
            Text(
              '${change.kpiName} · ${monthName(change.periodStart)}',
              style: TextStyle(color: muted),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Text(
          change.summary,
          style: Theme.of(context).textTheme.titleMedium
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        if (change.explainedBySeason) ...[
          const SizedBox(height: 8),
          Text(
            'This is about what your busy and quiet seasons lead you to expect.',
            style: TextStyle(color: muted),
          ),
        ],
        const SectionTitle('Why it happened'),
        if (diagnosis == null) ...[
          Text(
            'Nobody has looked into this one yet.',
            style: TextStyle(color: muted),
          ),
          if (_canWork) ...[
            const SizedBox(height: 12),
            FilledButton(
              onPressed: _busy ? null : _explain,
              child: const Text('Work out why'),
            ),
          ],
        ] else ...[
          Text(
            diagnosis.headline,
            style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16),
          ),
          const SizedBox(height: 6),
          Text(diagnosis.summary),
          const SizedBox(height: 8),
          Text(
            diagnosis.confidence == null
                ? 'There is not enough evidence to say how sure we are.'
                : 'How sure we are: ${diagnosis.confidenceLabel} (${diagnosis.confidence} out of 100).',
            style: TextStyle(color: muted),
          ),
          if (diagnosis.confidenceNote != null)
            Text(
              diagnosis.confidenceNote!,
              style: TextStyle(color: muted, fontSize: 12),
            ),
          const SectionTitle('The evidence'),
          for (final e in diagnosis.evidence)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Tag(_evidenceText[e.type] ?? e.type),
                  const SizedBox(width: 10),
                  Expanded(child: Text(e.statement)),
                ],
              ),
            ),
          if (_canWork)
            TextButton(
              onPressed: _busy ? null : _explain,
              child: const Text('Work it out again'),
            ),
        ],
        const SectionTitle('What to do about it'),
        OutlinedButton(
          onPressed: _toIdea,
          style: OutlinedButton.styleFrom(
            minimumSize: const Size.fromHeight(50),
          ),
          child: const Text('See the ideas'),
        ),
      ],
    );
  }
}
