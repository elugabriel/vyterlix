import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_reports.dart';
import '../widgets/common.dart';

/// Your business's figures written up to read. A report is kept exactly as it was written.
class ReportsScreen extends StatefulWidget {
  const ReportsScreen({super.key, required this.business});

  final Business business;

  @override
  State<ReportsScreen> createState() => _ReportsScreenState();
}

class _ReportsScreenState extends State<ReportsScreen> {
  List<ReportKind>? _kinds;
  List<ReportRunSummary> _history = [];
  String? _error;
  String? _writing; // id of the report being written
  String? _problem;

  String get _base => '/organizations/${widget.business.id}/reports';
  bool get _canWrite => widget.business.role != 'viewer';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final kinds = await api.get(_base) as List;
      final runs = await api.get('$_base/runs') as List;
      if (!mounted) return;
      setState(() {
        _kinds = [
          for (final k in kinds) ReportKind.fromJson(k as Map<String, dynamic>),
        ];
        _history = [
          for (final r in runs)
            ReportRunSummary.fromJson(r as Map<String, dynamic>),
        ];
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _open(String runId) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) =>
            ReportViewScreen(business: widget.business, runId: runId),
      ),
    );
  }

  Future<void> _write(ReportKind kind) async {
    setState(() {
      _writing = kind.id;
      _problem = null;
    });
    try {
      final run = await context.read<ApiClient>().post(
        '$_base/${kind.id}/generate',
      ) as Map<String, dynamic>;
      if (!mounted) return;
      await _load();
      if (mounted) await _open('${run['id']}');
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _writing = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Reports',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : _kinds == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  Text(
                    'Choose a report and write it now. It is kept exactly as it was written, so you can always come back to it, and it is only ever shown to you.',
                    style: TextStyle(color: muted),
                  ),
                  const SizedBox(height: 16),
                  if (_problem != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Text(
                        _problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  for (final kind in _kinds!) ...[
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              kind.name,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                                fontSize: 16,
                              ),
                            ),
                            const SizedBox(height: 6),
                            Text(kind.description),
                            const SizedBox(height: 6),
                            Text(
                              kind.covers,
                              style: TextStyle(color: muted, fontSize: 13),
                            ),
                            const SizedBox(height: 12),
                            Wrap(
                              spacing: 12,
                              runSpacing: 8,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                if (_canWrite)
                                  FilledButton(
                                    key: ValueKey('write-${kind.id}'),
                                    onPressed: _writing == null
                                        ? () => _write(kind)
                                        : null,
                                    child: Text(
                                      _writing == kind.id
                                          ? 'Writing...'
                                          : 'Write it now',
                                    ),
                                  ),
                                if (kind.lastRun != null)
                                  TextButton(
                                    onPressed: () => _open(kind.lastRun!.id),
                                    child: Text(
                                      'Open the latest (${ukDateTime(kind.lastRun!.generatedAt)})',
                                    ),
                                  )
                                else
                                  Text(
                                    'Nothing written for you yet',
                                    style: TextStyle(color: muted),
                                  ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                  ],
                  const SectionTitle('Reports written for you'),
                  if (_history.isEmpty)
                    Text(
                      'None yet. Write one above.',
                      style: TextStyle(color: muted),
                    ),
                  for (final run in _history) ...[
                    Card(
                      child: ListTile(
                        title: Text(run.title),
                        subtitle: Text(
                          '${run.periodStart.substring(0, 7)} to ${run.periodEnd.substring(0, 7)} · ${ukDateTime(run.generatedAt)} · ${run.how}',
                        ),
                        trailing: const Icon(Icons.chevron_right_rounded),
                        onTap: () => _open(run.id),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
                ],
              ),
            ),
    );
  }
}

/// One report, as it was written.
class ReportViewScreen extends StatefulWidget {
  const ReportViewScreen({
    super.key,
    required this.business,
    required this.runId,
  });

  final Business business;
  final String runId;

  @override
  State<ReportViewScreen> createState() => _ReportViewScreenState();
}

class _ReportViewScreenState extends State<ReportViewScreen> {
  ReportRun? _run;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(
        '/organizations/${widget.business.id}/reports/runs/${widget.runId}',
      );
      if (mounted) {
        setState(() => _run = ReportRun.fromJson(data as Map<String, dynamic>));
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final run = _run;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Report',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : run == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  run.title,
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 8),
                for (final fact in run.facts)
                  Text(fact, style: TextStyle(color: muted)),
                for (final s in run.sections) ...[
                  const SizedBox(height: 20),
                  Text(
                    s.heading,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  for (final p in s.paragraphs)
                    Padding(
                      padding: const EdgeInsets.only(top: 6),
                      child: Text(p),
                    ),
                  for (final b in s.bullets)
                    Padding(
                      padding: const EdgeInsets.only(top: 6, left: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('•  '),
                          Expanded(child: Text(b)),
                        ],
                      ),
                    ),
                  if (s.columns.isNotEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: 10),
                      child: Card(
                        child: SingleChildScrollView(
                          scrollDirection: Axis.horizontal,
                          child: DataTable(
                            columns: [
                              for (final c in s.columns)
                                DataColumn(label: Text(c)),
                            ],
                            rows: [
                              for (final r in s.rows)
                                DataRow(
                                  cells: [for (final c in r) DataCell(Text(c))],
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                ],
                const SizedBox(height: 20),
                for (final n in run.notes)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: Text(
                      n,
                      style: TextStyle(color: muted, fontSize: 13),
                    ),
                  ),
              ],
            ),
    );
  }
}
