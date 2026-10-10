import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_data.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'upload_screen.dart';

const statusText = {
  'uploaded': 'Uploaded',
  'mapped': 'Columns matched',
  'validated': 'Checked',
  'importing': 'Importing',
  'imported': 'Imported',
  'failed': 'Failed',
  'undone': 'Undone',
};

Color statusColor(String status) {
  switch (status) {
    case 'imported':
    case 'validated':
      return Palette.ok;
    case 'failed':
      return Palette.bad;
    case 'mapped':
    case 'uploaded':
    case 'importing':
      return Palette.warn;
    default:
      return Palette.lightMuted;
  }
}

String fileSize(int bytes) => bytes >= 1048576
    ? '${(bytes / 1048576).toStringAsFixed(1)} MB'
    : '${bytes < 1024 ? 1 : (bytes / 1024).round()} KB';

/// Every file that has been brought in, newest first. Open one to carry on with it or undo it.
class ImportsScreen extends StatefulWidget {
  const ImportsScreen({super.key, required this.business});

  final Business business;

  @override
  State<ImportsScreen> createState() => _ImportsScreenState();
}

class _ImportsScreenState extends State<ImportsScreen> {
  List<ImportRecord>? _imports;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/imports';

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
        query: {'limit': '50'},
      );
      if (mounted) {
        setState(
          () => _imports = [
            for (final i in data as List)
              ImportRecord.fromJson(i as Map<String, dynamic>),
          ],
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _open(ImportRecord record) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => UploadScreen(business: widget.business, resume: record),
      ),
    );
    if (mounted) await _load();
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Import history',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : _imports == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  if (_imports!.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 32),
                      child: Center(
                        child: Text(
                          'Nothing has been uploaded yet.',
                          style: TextStyle(color: muted),
                        ),
                      ),
                    ),
                  for (final record in _imports!) ...[
                    Card(
                      child: InkWell(
                        borderRadius: BorderRadius.circular(16),
                        onTap: () => _open(record),
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
                                    statusText[record.status] ?? record.status,
                                    color: statusColor(record.status),
                                  ),
                                  Text(
                                    datasetLabel(record.dataset),
                                    style: TextStyle(
                                      color: muted,
                                      fontSize: 13,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 8),
                              Text(
                                record.filename,
                                style: const TextStyle(
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                              const SizedBox(height: 4),
                              Text(
                                '${record.rowCount} rows · ${ukDateTime(record.createdAt)}${record.uploadedBy == null ? '' : ' · ${record.uploadedBy}'}',
                                style: TextStyle(color: muted, fontSize: 13),
                              ),
                              if (record.status == 'imported')
                                Text(
                                  '${record.importedCount} rows added to your data.',
                                  style: TextStyle(color: muted, fontSize: 13),
                                ),
                            ],
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 12),
                  ],
                ],
              ),
            ),
    );
  }
}
