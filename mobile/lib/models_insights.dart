// Key figures, business health, forecasts and changes, as the server sends them.

double? _number(dynamic value) =>
    value == null ? null : double.tryParse('$value');

class KpiValue {
  const KpiValue({
    required this.periodStart,
    required this.isComplete,
    required this.status,
    required this.value,
    required this.previousValue,
    required this.changePct,
    required this.dataQuality,
  });

  factory KpiValue.fromJson(Map<String, dynamic> json) => KpiValue(
    periodStart: '${json['period_start']}',
    isComplete: json['is_complete'] == true,
    status: '${json['status']}',
    value: json['value'] == null ? null : '${json['value']}',
    previousValue: json['previous_value'] == null
        ? null
        : '${json['previous_value']}',
    changePct: _number(json['change_pct']),
    dataQuality: json['data_quality'] as int?,
  );

  final String periodStart;
  final bool isComplete;

  /// ok, no_data or undefined
  final String status;
  final String? value;
  final String? previousValue;
  final double? changePct;
  final int? dataQuality;
}

class Kpi {
  const Kpi({
    required this.code,
    required this.name,
    required this.description,
    required this.category,
    required this.unit,
    required this.direction,
    required this.requires,
    required this.latest,
    required this.current,
  });

  factory Kpi.fromJson(Map<String, dynamic> json) => Kpi(
    code: '${json['code']}',
    name: '${json['name']}',
    description: '${json['description']}',
    category: '${json['category']}',
    unit: '${json['unit']}',
    direction: '${json['direction']}',
    requires: [for (final r in (json['requires'] as List? ?? const [])) '$r'],
    latest: json['latest'] is Map<String, dynamic>
        ? KpiValue.fromJson(json['latest'] as Map<String, dynamic>)
        : null,
    current: json['current'] is Map<String, dynamic>
        ? KpiValue.fromJson(json['current'] as Map<String, dynamic>)
        : null,
  );

  final String code;
  final String name;
  final String description;

  /// financial, sales, customer or inventory
  final String category;
  final String unit;

  /// up_good, down_good or neutral
  final String direction;
  final List<String> requires;
  final KpiValue? latest;
  final KpiValue? current;
}

class KpiHistory {
  const KpiHistory({
    required this.name,
    required this.description,
    required this.unit,
    required this.direction,
    required this.requires,
    required this.formula,
    required this.values,
  });

  factory KpiHistory.fromJson(Map<String, dynamic> json) => KpiHistory(
    name: '${json['name']}',
    description: '${json['description']}',
    unit: '${json['unit']}',
    direction: '${json['direction']}',
    requires: [for (final r in (json['requires'] as List? ?? const [])) '$r'],
    formula: '${json['formula']}',
    values: [
      for (final v in (json['values'] as List? ?? const []))
        KpiValue.fromJson(v as Map<String, dynamic>),
    ],
  );

  final String name;
  final String description;
  final String unit;
  final String direction;
  final List<String> requires;
  final String formula;

  /// Oldest first.
  final List<KpiValue> values;
}

class HealthMetric {
  const HealthMetric({
    required this.name,
    required this.score,
    required this.text,
  });

  factory HealthMetric.fromJson(Map<String, dynamic> json) => HealthMetric(
    name: '${json['name']}',
    score: _number(json['score']) ?? 0,
    text: '${json['text']}',
  );

  final String name;
  final double score;
  final String text;
}

class HealthComponent {
  const HealthComponent({
    required this.label,
    required this.score,
    required this.status,
    required this.trend,
    required this.explanation,
    required this.metrics,
  });

  factory HealthComponent.fromJson(Map<String, dynamic> json) =>
      HealthComponent(
        label: '${json['label']}',
        score: json['score'] as int?,
        status: '${json['status']}',
        trend: json['trend'] as String?,
        explanation: '${json['explanation']}',
        metrics: [
          for (final m in (json['metrics'] as List? ?? const []))
            HealthMetric.fromJson(m as Map<String, dynamic>),
        ],
      );

  final String label;
  final int? score;
  final String status;

  /// up, down or flat
  final String? trend;
  final String explanation;

  /// Weakest first.
  final List<HealthMetric> metrics;
}

class HealthReport {
  const HealthReport({
    required this.periodStart,
    required this.overallScore,
    required this.status,
    required this.previousScore,
    required this.trend,
    required this.coveragePct,
    required this.dataQuality,
    required this.explanation,
    required this.components,
  });

  factory HealthReport.fromJson(Map<String, dynamic> json) => HealthReport(
    periodStart: '${json['period_start']}',
    overallScore: json['overall_score'] as int?,
    status: '${json['status']}',
    previousScore: json['previous_score'] as int?,
    trend: json['trend'] as String?,
    coveragePct: (json['coverage_pct'] as int?) ?? 0,
    dataQuality: json['data_quality'] as int?,
    explanation: '${json['explanation']}',
    components: [
      for (final c in (json['components'] as List? ?? const []))
        HealthComponent.fromJson(c as Map<String, dynamic>),
    ],
  );

  final String periodStart;
  final int? overallScore;
  final String status;
  final int? previousScore;
  final String? trend;
  final int coveragePct;
  final int? dataQuality;
  final String explanation;

  /// Biggest weight first.
  final List<HealthComponent> components;
}

class HealthPoint {
  const HealthPoint({
    required this.periodStart,
    required this.score,
    required this.status,
  });

  factory HealthPoint.fromJson(Map<String, dynamic> json) => HealthPoint(
    periodStart: '${json['period_start']}',
    score: json['overall_score'] as int?,
    status: '${json['status']}',
  );

  final String periodStart;
  final int? score;
  final String status;
}

class ForecastFigure {
  const ForecastFigure({
    required this.code,
    required this.name,
    required this.unit,
    required this.group,
  });

  factory ForecastFigure.fromJson(Map<String, dynamic> json) => ForecastFigure(
    code: '${json['code']}',
    name: '${json['name']}',
    unit: '${json['unit']}',
    group: '${json['group']}',
  );

  final String code;
  final String name;
  final String unit;
  final String group;
}

class Prediction {
  const Prediction({
    required this.periodStart,
    required this.value,
    required this.lower,
    required this.upper,
    required this.actual,
  });

  factory Prediction.fromJson(Map<String, dynamic> json) => Prediction(
    periodStart: '${json['period_start']}',
    value: double.parse('${json['value']}'),
    lower: double.parse('${json['lower']}'),
    upper: double.parse('${json['upper']}'),
    actual: _number(json['actual_value']),
  );

  final String periodStart;
  final double value;
  final double lower;
  final double upper;

  /// Filled in once the month has finished.
  final double? actual;
}

class Forecast {
  const Forecast({
    required this.name,
    required this.unit,
    required this.status,
    required this.intervalLevel,
    required this.methodName,
    required this.explanation,
    required this.predictions,
    required this.history,
  });

  factory Forecast.fromJson(Map<String, dynamic> json) => Forecast(
    name: '${json['kpi_name']}',
    unit: '${json['unit']}',
    status: '${json['status']}',
    intervalLevel: (json['interval_level'] as int?) ?? 80,
    methodName: json['method'] is Map<String, dynamic>
        ? '${(json['method'] as Map<String, dynamic>)['name']}'
        : null,
    explanation: '${json['explanation']}',
    predictions: [
      for (final p in (json['predictions'] as List? ?? const []))
        Prediction.fromJson(p as Map<String, dynamic>),
    ],
    history: [
      for (final h in (json['history'] as List? ?? const []))
        (
          '${(h as Map<String, dynamic>)['period_start']}',
          double.parse('${h['value']}'),
        ),
    ],
  );

  final String name;
  final String unit;

  /// ok or insufficient_data
  final String status;
  final int intervalLevel;
  final String? methodName;
  final String explanation;
  final List<Prediction> predictions;

  /// (month, value), oldest first.
  final List<(String, double)> history;
}

class ForecastAccuracy {
  const ForecastAccuracy({
    required this.enoughData,
    required this.headline,
    required this.verdict,
  });

  factory ForecastAccuracy.fromJson(Map<String, dynamic> json) =>
      ForecastAccuracy(
        enoughData: json['enough_data'] == true,
        headline: '${json['headline']}',
        verdict: json['verdict'] as String?,
      );

  final bool enoughData;
  final String headline;
  final String? verdict;
}

class StockNeed {
  const StockNeed({
    required this.name,
    required this.expectedUnits,
    required this.onHand,
    required this.daysOfCover,
    required this.status,
    required this.orderSuggested,
  });

  factory StockNeed.fromJson(Map<String, dynamic> json) => StockNeed(
    name: '${json['name']}',
    expectedUnits: (json['expected_units'] as int?) ?? 0,
    onHand: (json['on_hand'] as int?) ?? 0,
    daysOfCover: json['days_of_cover'] as int?,
    status: '${json['status']}',
    orderSuggested: (json['order_suggested'] as int?) ?? 0,
  );

  final String name;
  final int expectedUnits;
  final int onHand;
  final int? daysOfCover;

  /// order_now, watch or ok
  final String status;
  final int orderSuggested;
}

class StockRequirements {
  const StockRequirements({
    required this.headline,
    required this.note,
    required this.orderNow,
    required this.watch,
    required this.rows,
  });

  factory StockRequirements.fromJson(Map<String, dynamic> json) =>
      StockRequirements(
        headline: '${json['headline']}',
        note: '${json['note']}',
        orderNow: (json['order_now'] as int?) ?? 0,
        watch: (json['watch'] as int?) ?? 0,
        rows: [
          for (final r in (json['rows'] as List? ?? const []))
            StockNeed.fromJson(r as Map<String, dynamic>),
        ],
      );

  final String headline;
  final String note;
  final int orderNow;
  final int watch;

  /// Most urgent first.
  final List<StockNeed> rows;
}

class Change {
  const Change({
    required this.id,
    required this.kpiName,
    required this.periodStart,
    required this.kind,
    required this.direction,
    required this.severity,
    required this.effect,
    required this.summary,
    required this.status,
    required this.explainedBySeason,
  });

  factory Change.fromJson(Map<String, dynamic> json) => Change(
    id: '${json['id']}',
    kpiName: '${json['kpi_name']}',
    periodStart: '${json['period_start']}',
    kind: '${json['kind']}',
    direction: '${json['direction']}',
    severity: '${json['severity']}',
    effect: '${json['effect']}',
    summary: '${json['summary']}',
    status: '${json['status']}',
    explainedBySeason: json['explained_by_season'] == true,
  );

  final String id;
  final String kpiName;
  final String periodStart;

  /// material_change or anomaly
  final String kind;
  final String direction;

  /// notable or major
  final String severity;

  /// good, bad or neutral: what it means for the business
  final String effect;
  final String summary;

  /// open, dismissed or diagnosed
  final String status;
  final bool explainedBySeason;
}

class Evidence {
  const Evidence({required this.type, required this.statement});

  factory Evidence.fromJson(Map<String, dynamic> json) => Evidence(
    type: '${json['evidence_type']}',
    statement: '${json['statement']}',
  );

  /// fact, statistical, ai_interpretation or insufficient
  final String type;
  final String statement;
}

class Diagnosis {
  const Diagnosis({
    required this.status,
    required this.headline,
    required this.summary,
    required this.confidence,
    required this.confidenceLabel,
    required this.confidenceNote,
    required this.evidence,
  });

  factory Diagnosis.fromJson(Map<String, dynamic> json) => Diagnosis(
    status: '${json['status']}',
    headline: '${json['headline']}',
    summary: '${json['summary']}',
    confidence: json['confidence'] as int?,
    confidenceLabel: '${json['confidence_label']}',
    confidenceNote: json['confidence_note'] as String?,
    evidence: [
      for (final e in (json['evidence'] as List? ?? const []))
        Evidence.fromJson(e as Map<String, dynamic>),
    ],
  );

  /// ready or insufficient_evidence
  final String status;
  final String headline;
  final String summary;
  final int? confidence;

  /// high, medium, low or insufficient
  final String confidenceLabel;
  final String? confidenceNote;
  final List<Evidence> evidence;
}
