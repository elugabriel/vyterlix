class ReportRunSummary {
  const ReportRunSummary({
    required this.id,
    required this.title,
    required this.trigger,
    required this.periodStart,
    required this.periodEnd,
    required this.generatedAt,
    required this.emailed,
  });

  factory ReportRunSummary.fromJson(Map<String, dynamic> json) =>
      ReportRunSummary(
        id: '${json['id']}',
        title: '${json['title']}',
        trigger: '${json['trigger']}',
        periodStart: '${json['period_start']}',
        periodEnd: '${json['period_end']}',
        generatedAt: '${json['generated_at']}',
        emailed: json['emailed'] == true,
      );

  final String id;
  final String title;

  /// manual or scheduled
  final String trigger;
  final String periodStart;
  final String periodEnd;
  final String generatedAt;
  final bool emailed;

  /// "Sent to you", "Scheduled" or "By you", as the website says it.
  String get how => trigger == 'scheduled'
      ? (emailed ? 'Sent to you' : 'Scheduled')
      : 'By you';
}

class ReportKind {
  const ReportKind({
    required this.id,
    required this.name,
    required this.description,
    required this.months,
    required this.fixedMonths,
    required this.lastRun,
  });

  factory ReportKind.fromJson(Map<String, dynamic> json) => ReportKind(
    id: '${json['id']}',
    name: '${json['name']}',
    description: '${json['description']}',
    months: (json['months'] as num?)?.toInt() ?? 1,
    fixedMonths: json['fixed_months'] == true,
    lastRun: json['last_run'] == null
        ? null
        : ReportRunSummary.fromJson(json['last_run'] as Map<String, dynamic>),
  );

  final String id;
  final String name;
  final String description;
  final int months;
  final bool fixedMonths;
  final ReportRunSummary? lastRun;

  String get covers => fixedMonths
      ? 'Covers the latest month'
      : 'Covers the last $months months';
}

class ReportSection {
  const ReportSection({
    required this.heading,
    required this.paragraphs,
    required this.bullets,
    required this.columns,
    required this.rows,
  });

  factory ReportSection.fromJson(Map<String, dynamic> json) {
    final table = json['table'] as Map<String, dynamic>?;
    return ReportSection(
      heading: '${json['heading']}',
      paragraphs: [for (final p in json['paragraphs'] as List? ?? []) '$p'],
      bullets: [for (final b in json['bullets'] as List? ?? []) '$b'],
      columns: [for (final c in table?['columns'] as List? ?? []) '$c'],
      rows: [
        for (final r in table?['rows'] as List? ?? [])
          [for (final cell in r as List) cell == null ? '–' : '$cell'],
      ],
    );
  }

  final String heading;
  final List<String> paragraphs;
  final List<String> bullets;
  final List<String> columns;
  final List<List<String>> rows;
}

/// A report exactly as it was written.
class ReportRun {
  const ReportRun({
    required this.summary,
    required this.title,
    required this.facts,
    required this.sections,
    required this.notes,
  });

  factory ReportRun.fromJson(Map<String, dynamic> json) {
    final c = json['content'] as Map<String, dynamic>;
    return ReportRun(
      summary: ReportRunSummary.fromJson(json),
      title: '${c['title']}',
      facts: [for (final f in c['facts'] as List? ?? []) '$f'],
      sections: [
        for (final s in c['sections'] as List? ?? [])
          ReportSection.fromJson(s as Map<String, dynamic>),
      ],
      notes: [for (final n in c['notes'] as List? ?? []) '$n'],
    );
  }

  final ReportRunSummary summary;
  final String title;
  final List<String> facts;
  final List<ReportSection> sections;
  final List<String> notes;
}
