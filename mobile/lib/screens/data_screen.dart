import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../models.dart';
import '../models_data.dart';
import '../theme.dart';
import '../widgets/common.dart';
import '../widgets/severity.dart';
import 'imports_screen.dart';
import 'upload_screen.dart';

Color bandColor(String? band) {
  switch (band) {
    case 'good':
      return Palette.ok;
    case 'fair':
      return Palette.warn;
    case 'poor':
      return Palette.bad;
    default:
      return Palette.lightMuted;
  }
}

/// How complete and trustworthy the business's data is, and where to bring more in.
class DataScreen extends StatefulWidget {
  const DataScreen({super.key, required this.business});

  final Business business;

  @override
  State<DataScreen> createState() => _DataScreenState();
}

class _DataScreenState extends State<DataScreen> {
  DataQuality? _quality;
  String? _error;

  bool get _isOwner => widget.business.role == 'owner';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(
        '/organizations/${widget.business.id}/data-quality',
      );
      if (mounted) {
        setState(
          () => _quality = DataQuality.fromJson(data as Map<String, dynamic>),
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _go(Widget screen) async {
    await Navigator.of(context)
        .push(MaterialPageRoute<void>(builder: (_) => screen));
    if (mounted) await _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Your data',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final quality = _quality;
    if (quality == null) {
      return const Center(child: CircularProgressIndicator());
    }
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final color = bandColor(quality.band);
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Card(
            child: Padding(
              padding: const EdgeInsets.all(20),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Your data quality',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 8),
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.end,
                    children: [
                      Text(
                        quality.score == null ? '–' : '${quality.score}',
                        key: const ValueKey('data-score'),
                        style: TextStyle(
                          fontSize: 46,
                          fontWeight: FontWeight.w800,
                          color: color,
                          height: 1,
                        ),
                      ),
                      const SizedBox(width: 8),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 6),
                        child: Text(
                          'out of 100',
                          style: TextStyle(color: muted),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(quality.headline),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          if (_isOwner) ...[
            FilledButton.icon(
              onPressed: () => _go(UploadScreen(business: widget.business)),
              icon: const Icon(Icons.upload_file_rounded),
              label: const Text('Upload a file'),
            ),
            const SizedBox(height: 10),
            OutlinedButton.icon(
              onPressed: () => _go(ImportsScreen(business: widget.business)),
              style: OutlinedButton.styleFrom(
                minimumSize: const Size.fromHeight(50),
              ),
              icon: const Icon(Icons.history_rounded),
              label: const Text('Import history'),
            ),
          ] else
            Text(
              'Only the owner can add or change data.',
              style: TextStyle(color: muted),
            ),
          if (quality.issues.isNotEmpty) ...[
            const SectionTitle('What to fix first'),
            for (final issue in quality.issues.take(6)) ...[
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Tag(
                        issue.severity == 'critical'
                            ? 'Fix first'
                            : issue.severity == 'warning'
                            ? 'Should fix'
                            : 'Worth knowing',
                        color: severityColor(
                          issue.severity == 'critical'
                              ? 'high'
                              : issue.severity == 'warning'
                              ? 'medium'
                              : 'low',
                        ),
                      ),
                      const SizedBox(height: 8),
                      Text(issue.message),
                      const SizedBox(height: 4),
                      Text(issue.fix, style: TextStyle(color: muted)),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],
          ],
          if (quality.datasets.isNotEmpty) ...[
            const SectionTitle('How each kind of data is doing'),
            for (final d in quality.datasets) ...[
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(
                            child: Text(
                              d.label,
                              style: const TextStyle(
                                fontWeight: FontWeight.w700,
                              ),
                            ),
                          ),
                          Text(
                            d.score == null ? '–' : '${d.score}',
                            style: TextStyle(
                              fontWeight: FontWeight.w800,
                              color: bandColor(d.band),
                            ),
                          ),
                        ],
                      ),
                      Text(
                        '${d.records} records',
                        style: TextStyle(color: muted, fontSize: 13),
                      ),
                      if (d.score != null) ...[
                        const SizedBox(height: 8),
                        ClipRRect(
                          borderRadius: BorderRadius.circular(999),
                          child: LinearProgressIndicator(
                            value: d.score! / 100,
                            minHeight: 6,
                            color: bandColor(d.band),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 12),
            ],
          ],
        ],
      ),
    );
  }
}
