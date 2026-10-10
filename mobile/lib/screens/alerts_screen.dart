import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/severity.dart';

const _statusText = {
  'open': 'Open',
  'acknowledged': 'Being dealt with',
  'resolved': 'Closed',
};

const _eventText = {
  'raised': 'Raised',
  'repeated': 'Seen again',
  'acknowledged': 'Taken on',
  'resolved': 'Closed',
  'reopened': 'Opened again',
};

/// Everything that needed attention, how serious it was, and what happened to it.
class AlertsScreen extends StatefulWidget {
  const AlertsScreen({super.key, required this.business});

  final Business business;

  @override
  State<AlertsScreen> createState() => _AlertsScreenState();
}

class _AlertsScreenState extends State<AlertsScreen> {
  String _status = 'open';
  List<AlertSummary>? _alerts;
  AlertCounts? _counts;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/alerts';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final results = await Future.wait([
        api.get(_base, query: {'status': _status, 'limit': '100'}),
        api.get('$_base/summary'),
      ]);
      if (!mounted) return;
      setState(() {
        _alerts = [
          for (final item in results[0] as List)
            AlertSummary.fromJson(item as Map<String, dynamic>),
        ];
        _counts = AlertCounts.fromJson(results[1] as Map<String, dynamic>);
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _open(AlertSummary alert) async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) =>
            AlertDetailScreen(business: widget.business, alertId: alert.id),
      ),
    );
    if (changed == true) await _load();
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final alerts = _alerts;
    if (alerts == null) return const Center(child: CircularProgressIndicator());
    final counts = _counts;
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          if (counts != null)
            Text(
              counts.open == 0
                  ? 'Nothing is waiting for you.'
                  : '${counts.open} open${counts.seriousOpen > 0 ? ', ${counts.seriousOpen} of them serious' : ''}.',
              style: Theme.of(context).textTheme.titleMedium
                  ?.copyWith(fontWeight: FontWeight.w700),
            ),
          const SizedBox(height: 12),
          SegmentedButton<String>(
            showSelectedIcon: false,
            segments: [
              ButtonSegment(
                value: 'open',
                label: Text('Open${counts == null ? '' : ' ${counts.open}'}'),
              ),
              ButtonSegment(
                value: 'acknowledged',
                label: Text(
                  'Handling${counts == null ? '' : ' ${counts.acknowledged}'}',
                ),
              ),
              ButtonSegment(
                value: 'resolved',
                label: Text(
                  'Closed${counts == null ? '' : ' ${counts.resolved}'}',
                ),
              ),
            ],
            selected: {_status},
            onSelectionChanged: (selection) {
              setState(() {
                _status = selection.first;
                _alerts = null;
              });
              _load();
            },
          ),
          const SizedBox(height: 16),
          if (alerts.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 32),
              child: Center(
                child: Text(switch (_status) {
                  'open' => 'No open alerts. Nice and quiet.',
                  'acknowledged' => 'Nothing is being dealt with right now.',
                  _ => 'No closed alerts yet.',
                }, style: TextStyle(color: muted)),
              ),
            ),
          for (final alert in alerts) ...[
            _AlertCard(alert: alert, onTap: () => _open(alert)),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}

class _AlertCard extends StatelessWidget {
  const _AlertCard({required this.alert, required this.onTap});

  final AlertSummary alert;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = severityColor(alert.severity);
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
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
                            severityText[alert.severity] ?? alert.severity,
                            color: color,
                          ),
                          Text(
                            _statusText[alert.status] ?? alert.status,
                            style: TextStyle(color: muted, fontSize: 13),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      Text(
                        alert.title,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        alert.body,
                        style: TextStyle(color: muted),
                        maxLines: 3,
                        overflow: TextOverflow.ellipsis,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        alert.occurrences > 1
                            ? 'Seen ${alert.occurrences} times, last on ${ukDateTime(alert.lastSeenAt)}'
                            : 'Seen on ${ukDateTime(alert.firstSeenAt)}',
                        style: TextStyle(color: muted, fontSize: 12),
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

/// One alert in full: what it says, what has happened to it, and (for those who may) buttons to
/// take it on or close it.
class AlertDetailScreen extends StatefulWidget {
  const AlertDetailScreen({
    super.key,
    required this.business,
    required this.alertId,
  });

  final Business business;
  final String alertId;

  @override
  State<AlertDetailScreen> createState() => _AlertDetailScreenState();
}

class _AlertDetailScreenState extends State<AlertDetailScreen> {
  AlertDetail? _detail;
  String? _error;
  bool _busy = false;
  bool _changed = false;
  final _note = TextEditingController();

  String get _path =>
      '/organizations/${widget.business.id}/alerts/${widget.alertId}';

  /// A viewer can look but not act (the server would refuse anyway).
  bool get _canAct => widget.business.role != 'viewer';

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
          () => _detail = AlertDetail.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _act(String what) async {
    final text = _note.text.trim();
    setState(() => _busy = true);
    try {
      final data = await context.read<ApiClient>().post(
        '$_path/$what',
        body: text.isEmpty ? null : {'note': text},
      );
      if (!mounted) return;
      _note.clear();
      _changed = true;
      setState(
        () => _detail = AlertDetail.fromJson(data as Map<String, dynamic>),
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

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) Navigator.of(context).pop(_changed);
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text(
            'Alert',
            style: TextStyle(fontWeight: FontWeight.w700),
          ),
        ),
        body: _body(),
      ),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final detail = _detail;
    if (detail == null) return const Center(child: CircularProgressIndicator());
    final alert = detail.alert;
    final color = severityColor(alert.severity);
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Wrap(
          spacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Tag(severityText[alert.severity] ?? alert.severity, color: color),
            Tag(_statusText[alert.status] ?? alert.status),
          ],
        ),
        const SizedBox(height: 12),
        Text(
          alert.title,
          style: Theme.of(context).textTheme.titleLarge
              ?.copyWith(fontWeight: FontWeight.w700),
        ),
        const SizedBox(height: 8),
        Text(alert.body),
        const SizedBox(height: 8),
        Text(
          alert.occurrences > 1
              ? 'Seen ${alert.occurrences} times since ${ukDateTime(alert.firstSeenAt)}.'
              : 'First seen ${ukDateTime(alert.firstSeenAt)}.',
          style: TextStyle(color: muted),
        ),
        if (alert.acknowledgedBy != null && alert.status == 'acknowledged')
          Text(
            'Taken on by ${alert.acknowledgedBy}.',
            style: TextStyle(color: muted),
          ),
        if (alert.link != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              'To see why it happened and what to do, open the same alert on the website.',
              style: TextStyle(color: muted),
            ),
          ),
        if (_canAct && alert.status != 'resolved') ...[
          const SectionTitle('What do you want to do?'),
          TextField(
            controller: _note,
            maxLines: 2,
            maxLength: 500,
            decoration: const InputDecoration(labelText: 'A note (optional)'),
          ),
          const SizedBox(height: 8),
          if (alert.status == 'open') ...[
            FilledButton(
              onPressed: _busy ? null : () => _act('acknowledge'),
              child: const Text("I'm on it"),
            ),
            const SizedBox(height: 10),
          ],
          OutlinedButton(
            onPressed: _busy ? null : () => _act('resolve'),
            style: OutlinedButton.styleFrom(
              minimumSize: const Size.fromHeight(50),
            ),
            child: const Text('Close it'),
          ),
        ],
        if (!_canAct && alert.status != 'resolved')
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              'Only owners and managers can take on or close an alert.',
              style: TextStyle(color: muted),
            ),
          ),
        const SectionTitle('What has happened'),
        for (final event in detail.events.reversed)
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
                        '${_eventText[event.kind] ?? event.kind}${event.user == null ? '' : ' by ${event.user}'}',
                        style: const TextStyle(fontWeight: FontWeight.w600),
                      ),
                      Text(
                        ukDateTime(event.createdAt),
                        style: TextStyle(color: muted, fontSize: 12),
                      ),
                      if (event.note != null) Text(event.note!),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}
