import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../files.dart';
import '../format.dart';
import '../models.dart';
import '../models_data.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'imports_screen.dart' show fileSize, statusColor, statusText;

const _recordLabel = {
  'sales': 'sales',
  'sale_lines': 'sale lines',
  'expenses': 'expenses',
  'customers': 'customers',
  'suppliers': 'suppliers',
  'products': 'products',
  'stock_movements': 'stock movements',
  'business_list_items': 'list entries (channels, categories...)',
};

String describeCounts(Map<String, int> counts) {
  final parts = [
    for (final e in counts.entries)
      if (e.value > 0)
        '${NumberFormat.decimalPattern('en_GB').format(e.value)} ${_recordLabel[e.key] ?? e.key}',
  ];
  return parts.isEmpty ? 'nothing' : parts.join(', ');
}

enum _Step { choose, file, mapping, check, imported, undone, other }

const _steps = ['Your file', 'Match columns', 'Check', 'Import'];

/// Bring a file in: choose it, check the columns are matched, check every row, import, and undo
/// if it was a mistake. Nothing is added to the data until the person says so. Opening an upload
/// from the history carries on from where it got to.
class UploadScreen extends StatefulWidget {
  const UploadScreen({
    super.key,
    required this.business,
    this.resume,
    this.pollEvery = const Duration(milliseconds: 1500),
  });

  final Business business;

  /// An earlier upload to carry on with (or look at).
  final ImportRecord? resume;

  /// How often to ask a background job how it is getting on.
  final Duration pollEvery;

  @override
  State<UploadScreen> createState() => _UploadScreenState();
}

class _UploadScreenState extends State<UploadScreen> {
  _Step _step = _Step.choose;
  bool _loading = false;
  bool _busy = false;
  String? _busyText;
  int? _percent;
  String? _error;

  String _dataset = 'sales';
  ImportRecord? _record;
  FilePreview? _preview;
  EarlierUpload? _duplicate;
  Mapping? _mapping;
  final Map<String, String?> _choice = {};
  bool? _vatInclusive;
  String? _vatRate;
  final _saveAs = TextEditingController();
  Validation? _validation;
  ImportResult? _result;
  Map<String, int>? _added;
  UndoSummary? _undone;

  String get _imports => '/organizations/${widget.business.id}/imports';
  String get _org => '/organizations/${widget.business.id}';

  ApiClient get _api => context.read<ApiClient>();

  @override
  void initState() {
    super.initState();
    if (widget.resume != null) {
      _loading = true;
      _resume(widget.resume!);
    }
  }

  @override
  void dispose() {
    _saveAs.dispose();
    super.dispose();
  }

  // --- carrying on from where an upload got to ------------------------------------------------

  Future<void> _resume(ImportRecord start) async {
    try {
      final data =
          await _api.get('$_imports/${start.id}') as Map<String, dynamic>;
      final detail = UploadedImport.fromJson(data);
      _record = detail.record;
      _preview = detail.preview;
      _dataset = detail.record.dataset;
      switch (detail.record.status) {
        case 'uploaded':
          _step = _Step.file;
        case 'mapped':
          await _loadMapping();
          _step = _Step.check;
        case 'validated':
          await _loadMapping();
          _validation = Validation.fromJson(
            await _api.get('$_imports/${start.id}/validation')
                as Map<String, dynamic>,
          );
          _step = _Step.check;
        case 'imported':
          _added = await _loadAdded();
          _step = _Step.imported;
        case 'undone':
          _step = _Step.undone;
        default:
          _step = _Step.other;
      }
    } on ApiException catch (error) {
      _error = error.message;
    }
    if (mounted) setState(() => _loading = false);
  }

  Future<Map<String, int>> _loadAdded() async {
    final data = await _api.get(
      '$_imports/${_record!.id}/records',
    ) as Map<String, dynamic>;
    return {
      for (final e in (data['counts'] as Map).entries)
        '${e.key}': e.value as int,
    };
  }

  Future<void> _loadMapping() async {
    final data = await _api.get(
      '$_imports/${_record!.id}/mapping',
    ) as Map<String, dynamic>;
    final mapping = Mapping.fromJson(data);
    _mapping = mapping;
    _choice.clear();
    final from = mapping.mapping.isNotEmpty
        ? mapping.mapping
        : mapping.suggested;
    for (final f in mapping.fields) {
      _choice[f.key] = from[f.key];
    }
    final inclusive = mapping.options['vat_inclusive'];
    _vatInclusive = inclusive is bool ? inclusive : null;
    final rate = mapping.options['default_vat_rate'];
    _vatRate = rate == null ? null : '$rate';
  }

  // --- helpers ---------------------------------------------------------------------------------

  void _fail(ApiException error) {
    if (mounted) setState(() => _error = error.message);
  }

  Future<void> _guard(String working, Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _busyText = working;
      _percent = null;
      _error = null;
    });
    try {
      await action();
    } on ApiException catch (error) {
      _fail(error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Run the check, the import or the undo: straight away when the file is small, in the
  /// background (and watched) when it is big.
  Future<Map<String, dynamic>?> _run(
    String action,
    Future<dynamic> Function() direct,
  ) async {
    try {
      return await direct() as Map<String, dynamic>;
    } on ApiException catch (error) {
      if (error.code != 'use_background_job') rethrow;
    }
    final started = await _api.post(
      '$_imports/${_record!.id}/jobs',
      body: {'action': action},
    ) as Map<String, dynamic>;
    final id = started['id'];
    while (true) {
      await Future<void>.delayed(widget.pollEvery);
      if (!mounted) return null;
      final job = JobState.fromJson(
        await _api.get('$_org/jobs/$id') as Map<String, dynamic>,
      );
      setState(() => _percent = job.percent);
      if (job.status == 'failed') {
        throw ApiException(
          422,
          'job_failed',
          job.errorMessage ?? 'That did not work. Please try again.',
        );
      }
      if (job.status == 'succeeded') return null;
    }
  }

  // --- choosing and reading the file --------------------------------------------------------------

  Future<void> _chooseAndUpload() async {
    final picked = await context.read<FileChooser>().chooseSpreadsheet();
    if (picked == null || !mounted) return;
    if (picked.bytes.isEmpty) {
      setState(() => _error = 'Choose a file first.');
      return;
    }
    await _guard('Reading your file', () async {
      final data = await _api.postFile(
        _imports,
        fields: {'dataset': _dataset},
        fileField: 'file',
        bytes: picked.bytes,
        filename: picked.name,
      ) as Map<String, dynamic>;
      final created = UploadedImport.fromJson(data);
      setState(() {
        _record = created.record;
        _preview = created.preview;
        _duplicate = created.duplicateOf;
        _step = _Step.file;
      });
    });
  }

  Future<void> _chooseSheet(String sheet) =>
      _guard('Reading that sheet', () async {
        final data = await _api.patch(
          '$_imports/${_record!.id}',
          body: {'sheet_name': sheet},
        ) as Map<String, dynamic>;
        final detail = UploadedImport.fromJson(data);
        setState(() {
          _record = detail.record;
          _preview = detail.preview;
        });
      });

  Future<void> _toMapping() => _guard('Looking at your columns', () async {
    await _loadMapping();
    setState(() => _step = _Step.mapping);
  });

  // --- matching the columns -----------------------------------------------------------------------

  Future<void> _saveMapping() => _guard('Saving', () async {
    final mapping = _mapping!;
    final chosen = {
      for (final e in _choice.entries)
        if (e.value != null && e.value!.isNotEmpty) e.key: e.value,
    };
    final options = <String, dynamic>{};
    if (mapping.needsVatOptions) {
      if (_vatInclusive != null) options['vat_inclusive'] = _vatInclusive;
      if (_vatRate != null && _vatRate!.isNotEmpty) {
        options['default_vat_rate'] = _vatRate;
      }
    }
    final name = _saveAs.text.trim();
    await _api.put(
      '$_imports/${_record!.id}/mapping',
      body: {
        'mapping': chosen,
        'options': options,
        if (name.isNotEmpty) 'save_as': name,
      },
    );
    setState(() {
      _validation = null;
      _step = _Step.check;
    });
  });

  // --- checking the rows --------------------------------------------------------------------------

  Future<void> _check() => _guard('Checking every row', () async {
    final data = await _run(
      'validate',
      () => _api.post('$_imports/${_record!.id}/validate'),
    );
    final validation =
        data ??
        await _api.get('$_imports/${_record!.id}/validation')
            as Map<String, dynamic>;
    setState(() => _validation = Validation.fromJson(validation));
  });

  Future<void> _import() => _guard('Importing', () async {
    final data = await _run(
      'import',
      () => _api.post('$_imports/${_record!.id}/import'),
    );
    if (data != null) {
      final result = ImportResult.fromJson(data);
      setState(() {
        _result = result;
        _record = result.record;
        _added = result.created;
        _step = _Step.imported;
      });
    } else {
      final detail = UploadedImport.fromJson(
        await _api.get('$_imports/${_record!.id}') as Map<String, dynamic>,
      );
      final added = await _loadAdded();
      setState(() {
        _record = detail.record;
        _added = added;
        _step = _Step.imported;
      });
    }
  });

  Future<void> _undo() async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Undo this import?'),
        content: const Text(
          'Undo removes everything this import added. Your other data is not touched.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: FilledButton.styleFrom(minimumSize: const Size(120, 44)),
            child: const Text('Undo it'),
          ),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    await _guard('Undoing', () async {
      final data = await _run(
        'undo',
        () => _api.post('$_imports/${_record!.id}/undo'),
      );
      if (data != null) {
        _undone = UndoSummary.fromJson(data);
        _record = _undone!.record;
      }
      setState(() => _step = _Step.undone);
    });
  }

  // --- the screen --------------------------------------------------------------------------------

  int get _stepIndex => switch (_step) {
    _Step.choose || _Step.file => 0,
    _Step.mapping => 1,
    _Step.check => 2,
    _ => 3,
  };

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.resume == null ? 'Upload a file' : 'Your file',
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                if (_step != _Step.undone && _step != _Step.other)
                  _stepper(muted),
                if (_error != null) ...[
                  _ErrorBox(_error!),
                  const SizedBox(height: 16),
                ],
                if (_busy) _working(muted),
                if (!_busy) ..._content(muted),
              ],
            ),
    );
  }

  Widget _stepper(Color muted) => Padding(
    padding: const EdgeInsets.only(bottom: 16),
    child: Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (var i = 0; i < _steps.length; i++)
          Tag(
            '${i + 1}. ${_steps[i]}',
            color: i < _stepIndex
                ? Palette.ok
                : i == _stepIndex
                ? Palette.accent
                : null,
          ),
      ],
    ),
  );

  Widget _working(Color muted) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${_busyText ?? 'Working'}...${_percent == null ? '' : ' $_percent%'}',
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          const SizedBox(height: 12),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              minHeight: 8,
              value: _percent == null ? null : _percent! / 100,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Big files are done in the background. You can wait here.',
            style: TextStyle(color: muted, fontSize: 12),
          ),
        ],
      ),
    ),
  );

  List<Widget> _content(Color muted) {
    switch (_step) {
      case _Step.choose:
        return _chooseView(muted);
      case _Step.file:
        return _fileView(muted);
      case _Step.mapping:
        return _mappingView(muted);
      case _Step.check:
        return _checkView(muted);
      case _Step.imported:
        return _importedView(muted);
      case _Step.undone:
        return _undoneView(muted);
      case _Step.other:
        return _otherView(muted);
    }
  }

  Widget _heading(String text) => Padding(
    padding: const EdgeInsets.only(bottom: 8),
    child: Text(
      text,
      style: Theme.of(context).textTheme.titleMedium
          ?.copyWith(fontWeight: FontWeight.w700),
    ),
  );

  List<Widget> _chooseView(Color muted) => [
    _heading('What is in the file?'),
    Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final (key, label) in datasets)
          ChoiceChip(
            label: Text(label),
            selected: _dataset == key,
            showCheckmark: false,
            onSelected: (_) => setState(() => _dataset = key),
          ),
      ],
    ),
    const SizedBox(height: 12),
    Text(
      'A .csv or Excel .xlsx file, up to 25 MB. Nothing is added to your data until you have checked it.',
      style: TextStyle(color: muted),
    ),
    const SizedBox(height: 20),
    FilledButton.icon(
      onPressed: _chooseAndUpload,
      icon: const Icon(Icons.attach_file_rounded),
      label: const Text('Choose a file and read it'),
    ),
  ];

  List<Widget> _fileView(Color muted) {
    final record = _record!;
    final preview = _preview;
    return [
      Card(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                record.filename,
                style: const TextStyle(fontWeight: FontWeight.w700),
              ),
              const SizedBox(height: 4),
              Wrap(
                spacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(
                    '${datasetLabel(record.dataset)} · ${fileSize(record.fileSizeBytes)}',
                    style: TextStyle(color: muted),
                  ),
                  Tag(
                    statusText[record.status] ?? record.status,
                    color: statusColor(record.status),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
      if (_duplicate != null) ...[
        const SizedBox(height: 12),
        _InfoBox(
          'This looks like a file you uploaded before: “${_duplicate!.filename}” on ${ukDate(_duplicate!.createdAt)} (${(statusText[_duplicate!.status] ?? _duplicate!.status).toLowerCase()}). You can carry on, but rows already in your data will be skipped.',
        ),
      ],
      if (preview == null)
        Padding(
          padding: const EdgeInsets.only(top: 16),
          child: Text(
            'The original file has been deleted, so it cannot be read again.',
            style: TextStyle(color: muted),
          ),
        )
      else ...[
        if (preview.needsSheet) ...[
          const SizedBox(height: 16),
          _heading('Which sheet?'),
          Text(
            'This workbook has more than one sheet. Choose the one with your data.',
            style: TextStyle(color: muted),
          ),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            children: [
              for (final sheet in preview.sheets)
                ChoiceChip(
                  label: Text(sheet),
                  selected: record.sheetName == sheet,
                  showCheckmark: false,
                  onSelected: (_) => _chooseSheet(sheet),
                ),
            ],
          ),
        ] else ...[
          const SizedBox(height: 16),
          Text(
            'We read ${NumberFormat.decimalPattern('en_GB').format(preview.rowCount)} rows. Here are the first few. Check that the column headings look right.',
            style: TextStyle(color: muted),
          ),
          const SizedBox(height: 12),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: DataTable(
              columns: [
                for (final h in preview.headers)
                  DataColumn(
                    label: Text(
                      h,
                      style: const TextStyle(fontWeight: FontWeight.w700),
                    ),
                  ),
              ],
              rows: [
                for (final row in preview.sampleRows)
                  DataRow(
                    cells: [
                      for (var i = 0; i < preview.headers.length; i++)
                        DataCell(Text(i < row.length ? row[i] : '')),
                    ],
                  ),
              ],
            ),
          ),
          const SizedBox(height: 20),
          FilledButton(
            onPressed: _toMapping,
            child: const Text('Match the columns'),
          ),
        ],
      ],
    ];
  }

  List<Widget> _mappingView(Color muted) {
    final mapping = _mapping!;
    final byKey = {for (final f in mapping.fields) f.key: f.label};
    return [
      _heading('Match your columns'),
      Text(
        mapping.suggestionFrom == 'saved'
            ? 'We matched these from a mapping you saved. Check they are right.'
            : 'We have made a first guess at which column is which. Check them, and change any that are wrong.',
        style: TextStyle(color: muted),
      ),
      const SizedBox(height: 16),
      for (final f in mapping.fields) ...[
        DropdownButtonFormField<String>(
          key: ValueKey('map-${f.key}'),
          initialValue: mapping.headers.contains(_choice[f.key])
              ? _choice[f.key]
              : null,
          isExpanded: true,
          decoration: InputDecoration(
            labelText: f.required ? '${f.label} *' : f.label,
            helperText: f.help,
            helperMaxLines: 3,
          ),
          items: [
            const DropdownMenuItem<String>(
              value: '',
              child: Text('(not in my file)'),
            ),
            for (final h in mapping.headers)
              DropdownMenuItem<String>(
                value: h,
                child: Text(h, overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: (value) => setState(() => _choice[f.key] = value),
        ),
        if (f.oneOf != null)
          Padding(
            padding: const EdgeInsets.only(top: 4, bottom: 4),
            child: Text(
              'At least one of: ${f.oneOf!.map((k) => byKey[k] ?? k).join(', ')}.',
              style: TextStyle(color: muted, fontSize: 12),
            ),
          ),
        const SizedBox(height: 14),
      ],
      if (mapping.needsVatOptions) ...[
        _heading('About the amounts in this file'),
        const Text('Do the amounts include VAT? *'),
        RadioGroup<bool>(
          groupValue: _vatInclusive,
          onChanged: (v) => setState(() => _vatInclusive = v),
          child: const Column(
            children: [
              RadioListTile<bool>(
                contentPadding: EdgeInsets.zero,
                value: true,
                title: Text('Yes, amounts include VAT'),
              ),
              RadioListTile<bool>(
                contentPadding: EdgeInsets.zero,
                value: false,
                title: Text('No, amounts are before VAT'),
              ),
            ],
          ),
        ),
        DropdownButtonFormField<String>(
          key: const ValueKey('vat-rate'),
          initialValue: _vatRate,
          decoration: const InputDecoration(
            labelText: 'VAT rate',
            helperText: 'Only needed if your file has no VAT column. UK VAT rates only.',
          ),
          items: [
            const DropdownMenuItem<String>(
              value: '',
              child: Text('My file has a VAT column'),
            ),
            for (final r in mapping.vatRates)
              DropdownMenuItem<String>(value: r, child: Text('$r%')),
          ],
          onChanged: (v) => setState(() => _vatRate = v),
        ),
        const SizedBox(height: 14),
      ],
      TextField(
        controller: _saveAs,
        maxLength: 100,
        decoration: const InputDecoration(
          labelText: 'Remember this as... (optional)',
          helperText: 'Next time a file from the same place will match itself, for example "Till export".',
        ),
      ),
      const SizedBox(height: 16),
      FilledButton(
        onPressed: _saveMapping,
        child: const Text('Save and continue'),
      ),
      const SizedBox(height: 10),
      OutlinedButton(
        onPressed: () => setState(() => _step = _Step.file),
        style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
        child: const Text('Back'),
      ),
    ];
  }

  List<Widget> _checkView(Color muted) {
    final v = _validation;
    if (v == null) {
      return [
        _heading('Columns matched'),
        Text(
          'Next we check every row. Nothing is added to your data yet.',
          style: TextStyle(color: muted),
        ),
        const SizedBox(height: 16),
        FilledButton(onPressed: _check, child: const Text('Check the rows')),
        const SizedBox(height: 10),
        OutlinedButton(
          onPressed: _toMapping,
          style: OutlinedButton.styleFrom(
            minimumSize: const Size.fromHeight(50),
          ),
          child: const Text('Change the columns'),
        ),
      ];
    }
    final n = NumberFormat.decimalPattern('en_GB');
    return [
      _heading('Check results'),
      Wrap(
        spacing: 12,
        runSpacing: 12,
        children: [
          _tile(n.format(v.rows), 'rows checked'),
          _tile(n.format(v.valid), 'ready to import'),
          _tile(n.format(v.invalid), 'have a problem'),
          _tile(n.format(v.duplicate), 'repeats (skipped)'),
        ],
      ),
      if (v.net != null) ...[
        const SizedBox(height: 12),
        Text(
          'The ${n.format(v.valid)} rows ready to import add up to ${gbp(v.net!)} before VAT (${gbp(v.vat!)} VAT, ${gbp(v.gross!)} in total)${v.dateFrom == null ? '.' : ', dated ${ukDate(v.dateFrom!)} to ${ukDate(v.dateTo!)}.'}',
        ),
      ],
      for (final w in v.warnings) ...[const SizedBox(height: 12), _InfoBox(w)],
      if (v.problems.isNotEmpty) ...[
        const SectionTitle('What needs fixing'),
        for (final p in v.problems)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(p.example),
                  const SizedBox(height: 4),
                  Text(
                    '${p.count} row${p.count == 1 ? '' : 's'}, first: ${p.rows.join(', ')}',
                    style: TextStyle(color: muted, fontSize: 13),
                  ),
                ],
              ),
            ),
          ),
        const SizedBox(height: 8),
        Text(
          'Rows with a problem are skipped. To include them, fix the file and upload it again.',
          style: TextStyle(color: muted),
        ),
      ],
      const SizedBox(height: 16),
      FilledButton(
        onPressed: v.canImport ? _import : null,
        child: Text('Import ${n.format(v.valid)} rows'),
      ),
      const SizedBox(height: 10),
      OutlinedButton(
        onPressed: _check,
        style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
        child: const Text('Check again'),
      ),
    ];
  }

  Widget _tile(String value, String label) => SizedBox(
    width: 150,
    child: Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              value,
              style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700),
            ),
            Text(
              label,
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
                fontSize: 12,
              ),
            ),
          ],
        ),
      ),
    ),
  );

  List<Widget> _importedView(Color muted) {
    final record = _record!;
    final result = _result;
    return [
      _heading('Imported'),
      Text(
        '${NumberFormat.decimalPattern('en_GB').format(record.importedCount)} rows from “${record.filename}” are now in your data.',
      ),
      const SizedBox(height: 8),
      Text('Added: ${describeCounts(_added ?? const {})}.'),
      if (result != null &&
          (result.skippedDuplicates > 0 || result.skippedInvalid > 0))
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(
            'Skipped: ${result.skippedDuplicates} already in your data, ${result.skippedInvalid} that couldn\'t be read.',
            style: TextStyle(color: muted),
          ),
        ),
      const SizedBox(height: 8),
      Text(
        'Your figures update by themselves in a moment.',
        style: TextStyle(color: muted),
      ),
      const SectionTitle('Made a mistake?'),
      Text(
        'Undo removes everything this import added. Your other data is not touched.',
        style: TextStyle(color: muted),
      ),
      const SizedBox(height: 12),
      OutlinedButton(
        onPressed: _undo,
        style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(50)),
        child: const Text('Undo this import'),
      ),
    ];
  }

  List<Widget> _undoneView(Color muted) => [
    _heading('This import was undone'),
    if (_undone != null) Text('Removed: ${describeCounts(_undone!.removed)}.'),
    const SizedBox(height: 8),
    Text(
      'Everything it added has been removed. You can upload the file again after fixing it.',
      style: TextStyle(color: muted),
    ),
  ];

  List<Widget> _otherView(Color muted) {
    final record = _record!;
    return [
      _heading(record.filename),
      Tag(
        statusText[record.status] ?? record.status,
        color: statusColor(record.status),
      ),
      const SizedBox(height: 12),
      Text(
        record.status == 'failed'
            ? 'This import did not finish. Nothing was added to your data. Try uploading the file again.'
            : 'This import is still being worked on. Check back in a moment.',
        style: TextStyle(color: muted),
      ),
    ];
  }
}

class UndoSummary {
  const UndoSummary({required this.record, required this.removed});

  factory UndoSummary.fromJson(Map<String, dynamic> json) => UndoSummary(
    record: ImportRecord.fromJson(json['data_import'] as Map<String, dynamic>),
    removed: {
      for (final e in (json['removed'] as Map? ?? const {}).entries)
        '${e.key}': e.value as int,
    },
  );

  final ImportRecord record;
  final Map<String, int> removed;
}

class _ErrorBox extends StatelessWidget {
  const _ErrorBox(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Palette.bad.withValues(alpha: 0.08),
          border: const Border(left: BorderSide(color: Palette.bad, width: 4)),
        ),
        child: Text(message, style: const TextStyle(color: Palette.bad)),
      ),
    );
  }
}

class _InfoBox extends StatelessWidget {
  const _InfoBox(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(12),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: Palette.accent.withValues(alpha: 0.07),
          border: const Border(
            left: BorderSide(color: Palette.accent, width: 4),
          ),
        ),
        child: Text(message),
      ),
    );
  }
}
