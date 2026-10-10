// Your data: files brought in, how good the data is, and what a check found, as the server sends them.

const datasets = [
  ('sales', 'Sales'),
  ('expenses', 'Expenses'),
  ('customers', 'Customers'),
  ('suppliers', 'Suppliers'),
  ('products', 'Products'),
  ('stock_movements', 'Stock movements'),
];

String datasetLabel(String code) {
  for (final (key, label) in datasets) {
    if (key == code) return label;
  }
  return code;
}

class ImportRecord {
  const ImportRecord({
    required this.id,
    required this.dataset,
    required this.status,
    required this.filename,
    required this.fileSizeBytes,
    required this.rowCount,
    required this.validCount,
    required this.invalidCount,
    required this.duplicateCount,
    required this.importedCount,
    required this.createdAt,
    required this.importedAt,
    required this.undoneAt,
    required this.sheetName,
    required this.uploadedBy,
  });

  factory ImportRecord.fromJson(Map<String, dynamic> json) => ImportRecord(
    id: '${json['id']}',
    dataset: '${json['dataset']}',
    status: '${json['status']}',
    filename: '${json['original_filename']}',
    fileSizeBytes: (json['file_size_bytes'] as int?) ?? 0,
    rowCount: (json['row_count'] as int?) ?? 0,
    validCount: (json['valid_count'] as int?) ?? 0,
    invalidCount: (json['invalid_count'] as int?) ?? 0,
    duplicateCount: (json['duplicate_count'] as int?) ?? 0,
    importedCount: (json['imported_count'] as int?) ?? 0,
    createdAt: '${json['created_at']}',
    importedAt: json['imported_at'] as String?,
    undoneAt: json['undone_at'] as String?,
    sheetName: json['sheet_name'] as String?,
    uploadedBy: json['uploaded_by_name'] as String?,
  );

  final String id;
  final String dataset;

  /// uploaded, mapped, validated, importing, imported, failed or undone
  final String status;
  final String filename;
  final int fileSizeBytes;
  final int rowCount;
  final int validCount;
  final int invalidCount;
  final int duplicateCount;
  final int importedCount;
  final String createdAt;
  final String? importedAt;
  final String? undoneAt;
  final String? sheetName;
  final String? uploadedBy;
}

class EarlierUpload {
  const EarlierUpload({
    required this.filename,
    required this.status,
    required this.createdAt,
  });

  factory EarlierUpload.fromJson(Map<String, dynamic> json) => EarlierUpload(
    filename: '${json['original_filename']}',
    status: '${json['status']}',
    createdAt: '${json['created_at']}',
  );

  final String filename;
  final String status;
  final String createdAt;
}

class FilePreview {
  const FilePreview({
    required this.headers,
    required this.sampleRows,
    required this.rowCount,
    required this.sheets,
    required this.needsSheet,
  });

  factory FilePreview.fromJson(Map<String, dynamic> json) => FilePreview(
    headers: [for (final h in (json['headers'] as List? ?? const [])) '$h'],
    sampleRows: [
      for (final r in (json['sample_rows'] as List? ?? const []))
        [for (final c in (r as List)) '$c'],
    ],
    rowCount: (json['row_count'] as int?) ?? 0,
    sheets: [for (final s in (json['sheets'] as List? ?? const [])) '$s'],
    needsSheet: json['needs_sheet'] == true,
  );

  final List<String> headers;
  final List<List<String>> sampleRows;
  final int rowCount;
  final List<String> sheets;
  final bool needsSheet;
}

class UploadedImport {
  const UploadedImport({
    required this.record,
    required this.preview,
    required this.duplicateOf,
  });

  factory UploadedImport.fromJson(Map<String, dynamic> json) => UploadedImport(
    record: ImportRecord.fromJson(json),
    preview: json['preview'] is Map<String, dynamic>
        ? FilePreview.fromJson(json['preview'] as Map<String, dynamic>)
        : null,
    duplicateOf: json['duplicate_of'] is Map<String, dynamic>
        ? EarlierUpload.fromJson(json['duplicate_of'] as Map<String, dynamic>)
        : null,
  );

  final ImportRecord record;

  /// Null once the original file has been deleted.
  final FilePreview? preview;
  final EarlierUpload? duplicateOf;
}

class MappingField {
  const MappingField({
    required this.key,
    required this.label,
    required this.help,
    required this.required,
    required this.oneOf,
  });

  factory MappingField.fromJson(Map<String, dynamic> json) => MappingField(
    key: '${json['key']}',
    label: '${json['label']}',
    help: '${json['help']}',
    required: json['required'] == true,
    oneOf: json['one_of'] is List
        ? [for (final k in json['one_of'] as List) '$k']
        : null,
  );

  final String key;
  final String label;
  final String help;
  final bool required;

  /// "At least one of these fields is needed".
  final List<String>? oneOf;
}

class MappingIssue {
  const MappingIssue({required this.message});

  factory MappingIssue.fromJson(Map<String, dynamic> json) =>
      MappingIssue(message: '${json['message']}');

  final String message;
}

class Mapping {
  const Mapping({
    required this.fields,
    required this.headers,
    required this.mapping,
    required this.suggested,
    required this.options,
    required this.needsVatOptions,
    required this.vatRates,
    required this.issues,
    required this.ready,
    required this.suggestionFrom,
  });

  factory Mapping.fromJson(Map<String, dynamic> json) => Mapping(
    fields: [
      for (final f in (json['fields'] as List))
        MappingField.fromJson(f as Map<String, dynamic>),
    ],
    headers: [for (final h in (json['headers'] as List? ?? const [])) '$h'],
    mapping: {
      for (final e in (json['mapping'] as Map? ?? const {}).entries)
        '${e.key}': '${e.value}',
    },
    suggested: {
      for (final e in (json['suggested_mapping'] as Map? ?? const {}).entries)
        '${e.key}': '${e.value}',
    },
    options: Map<String, dynamic>.from(json['options'] as Map? ?? const {}),
    needsVatOptions: json['needs_vat_options'] == true,
    vatRates: [for (final r in (json['vat_rates'] as List? ?? const [])) '$r'],
    issues: [
      for (final i in (json['issues'] as List? ?? const []))
        MappingIssue.fromJson(i as Map<String, dynamic>),
    ],
    ready: json['ready'] == true,
    suggestionFrom: json['suggestion_from'] as String?,
  );

  final List<MappingField> fields;
  final List<String> headers;

  /// What is saved on this import: field to column heading.
  final Map<String, String> mapping;
  final Map<String, String> suggested;
  final Map<String, dynamic> options;
  final bool needsVatOptions;
  final List<String> vatRates;
  final List<MappingIssue> issues;
  final bool ready;

  /// saved (a mapping remembered under a name) or automatic.
  final String? suggestionFrom;
}

class ValidationProblem {
  const ValidationProblem({
    required this.example,
    required this.count,
    required this.rows,
  });

  factory ValidationProblem.fromJson(Map<String, dynamic> json) =>
      ValidationProblem(
        example: '${json['example']}',
        count: (json['count'] as int?) ?? 0,
        rows: [for (final r in (json['rows'] as List? ?? const [])) r as int],
      );

  final String example;
  final int count;
  final List<int> rows;
}

class Validation {
  const Validation({
    required this.rows,
    required this.valid,
    required this.invalid,
    required this.duplicate,
    required this.problems,
    required this.net,
    required this.vat,
    required this.gross,
    required this.dateFrom,
    required this.dateTo,
    required this.warnings,
    required this.canImport,
  });

  factory Validation.fromJson(Map<String, dynamic> json) {
    final totals = json['totals'] as Map<String, dynamic>?;
    return Validation(
      rows: (json['rows'] as int?) ?? 0,
      valid: (json['valid'] as int?) ?? 0,
      invalid: (json['invalid'] as int?) ?? 0,
      duplicate: (json['duplicate'] as int?) ?? 0,
      problems: [
        for (final p in (json['problems'] as List? ?? const []))
          ValidationProblem.fromJson(p as Map<String, dynamic>),
      ],
      net: totals == null ? null : '${totals['net']}',
      vat: totals == null ? null : '${totals['vat']}',
      gross: totals == null ? null : '${totals['gross']}',
      dateFrom: json['date_from'] as String?,
      dateTo: json['date_to'] as String?,
      warnings: [
        for (final w in (json['warnings'] as List? ?? const []))
          '${(w as Map<String, dynamic>)['message']}',
      ],
      canImport: json['can_import'] == true,
    );
  }

  final int rows;
  final int valid;
  final int invalid;
  final int duplicate;
  final List<ValidationProblem> problems;
  final String? net;
  final String? vat;
  final String? gross;
  final String? dateFrom;
  final String? dateTo;
  final List<String> warnings;
  final bool canImport;
}

class ImportResult {
  const ImportResult({
    required this.record,
    required this.created,
    required this.skippedDuplicates,
    required this.skippedInvalid,
  });

  factory ImportResult.fromJson(Map<String, dynamic> json) => ImportResult(
    record: ImportRecord.fromJson(json['data_import'] as Map<String, dynamic>),
    created: {
      for (final e in (json['created'] as Map? ?? const {}).entries)
        '${e.key}': e.value as int,
    },
    skippedDuplicates: (json['skipped_duplicates'] as int?) ?? 0,
    skippedInvalid: (json['skipped_invalid'] as int?) ?? 0,
  );

  final ImportRecord record;
  final Map<String, int> created;
  final int skippedDuplicates;
  final int skippedInvalid;
}

class JobState {
  const JobState({
    required this.status,
    required this.percent,
    required this.errorMessage,
  });

  factory JobState.fromJson(Map<String, dynamic> json) => JobState(
    status: '${json['status']}',
    percent: json['percent'] as int?,
    errorMessage: json['error_message'] as String?,
  );

  /// queued, running, succeeded or failed
  final String status;
  final int? percent;
  final String? errorMessage;

  bool get finished => status == 'succeeded' || status == 'failed';
}

class DatasetScore {
  const DatasetScore({
    required this.label,
    required this.records,
    required this.score,
    required this.band,
  });

  factory DatasetScore.fromJson(Map<String, dynamic> json) => DatasetScore(
    label: '${json['label']}',
    records: (json['records'] as int?) ?? 0,
    score: json['score'] as int?,
    band: json['band'] as String?,
  );

  final String label;
  final int records;
  final int? score;

  /// good, fair or poor
  final String? band;
}

class DataIssue {
  const DataIssue({
    required this.severity,
    required this.message,
    required this.fix,
  });

  factory DataIssue.fromJson(Map<String, dynamic> json) => DataIssue(
    severity: '${json['severity']}',
    message: '${json['message']}',
    fix: '${json['fix']}',
  );

  /// info, warning or critical
  final String severity;
  final String message;
  final String fix;
}

class DataQuality {
  const DataQuality({
    required this.score,
    required this.band,
    required this.headline,
    required this.datasets,
    required this.issues,
  });

  factory DataQuality.fromJson(Map<String, dynamic> json) => DataQuality(
    score: json['score'] as int?,
    band: json['band'] as String?,
    headline: '${json['headline']}',
    datasets: [
      for (final d in (json['datasets'] as List? ?? const []))
        DatasetScore.fromJson(d as Map<String, dynamic>),
    ],
    issues: [
      for (final i in (json['issues'] as List? ?? const []))
        DataIssue.fromJson(i as Map<String, dynamic>),
    ],
  );

  final int? score;
  final String? band;
  final String headline;
  final List<DatasetScore> datasets;
  final List<DataIssue> issues;
}
