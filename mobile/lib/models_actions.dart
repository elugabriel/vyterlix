// Actions (the work decided on) and recommendations (what Vyterlix suggests), as the server sends them.

class Person {
  const Person({required this.id, required this.name});

  factory Person.fromJson(Map<String, dynamic> json) =>
      Person(id: '${json['id']}', name: '${json['name']}');

  final String id;
  final String name;
}

Person? _person(dynamic json) =>
    json is Map<String, dynamic> ? Person.fromJson(json) : null;

class Progress {
  const Progress({
    required this.done,
    required this.total,
    required this.percent,
  });

  factory Progress.fromJson(Map<String, dynamic> json) => Progress(
    done: (json['done'] as int?) ?? 0,
    total: (json['total'] as int?) ?? 0,
    percent: (json['percent'] as int?) ?? 0,
  );

  final int done;
  final int total;
  final int percent;
}

class ActionSummary {
  const ActionSummary({
    required this.id,
    required this.title,
    required this.kpiName,
    required this.status,
    required this.statusLabel,
    required this.owner,
    required this.targetDate,
    required this.daysLate,
    required this.dueSoon,
    required this.progress,
  });

  factory ActionSummary.fromJson(Map<String, dynamic> json) => ActionSummary(
    id: '${json['id']}',
    title: '${json['title']}',
    kpiName: '${json['kpi_name']}',
    status: '${json['status']}',
    statusLabel: '${json['status_label']}',
    owner: _person(json['owner']),
    targetDate: json['target_date'] as String?,
    daysLate: (json['days_late'] as int?) ?? 0,
    dueSoon: json['due_soon'] == true,
    progress: Progress.fromJson(
      (json['progress'] as Map<String, dynamic>?) ?? const {},
    ),
  );

  final String id;
  final String title;
  final String kpiName;

  /// pending, accepted, in_progress, partially_completed, completed, cancelled or overdue
  final String status;
  final String statusLabel;
  final Person? owner;
  final String? targetDate;
  final int daysLate;
  final bool dueSoon;
  final Progress progress;
}

class ActionStep {
  const ActionStep({required this.text, required this.done});

  factory ActionStep.fromJson(Map<String, dynamic> json) =>
      ActionStep(text: '${json['text']}', done: json['done'] == true);

  final String text;
  final bool done;

  Map<String, dynamic> toJson() => {'text': text, 'done': done};
}

class ActionUpdate {
  const ActionUpdate({
    required this.kind,
    required this.fromStatus,
    required this.toStatus,
    required this.note,
    required this.user,
    required this.createdAt,
  });

  factory ActionUpdate.fromJson(Map<String, dynamic> json) => ActionUpdate(
    kind: '${json['kind']}',
    fromStatus: json['from_status'] as String?,
    toStatus: json['to_status'] as String?,
    note: json['note'] as String?,
    user: _person(json['user']),
    createdAt: '${json['created_at']}',
  );

  final String kind;
  final String? fromStatus;
  final String? toStatus;
  final String? note;
  final Person? user;
  final String createdAt;
}

class Decision {
  const Decision({
    required this.title,
    required this.description,
    required this.target,
    required this.kpiName,
    required this.unit,
    required this.baselinePeriod,
    required this.baselineValue,
    required this.expectedImpactValue,
    required this.acceptedBy,
    required this.acceptedAt,
  });

  factory Decision.fromJson(Map<String, dynamic> json) => Decision(
    title: '${json['title']}',
    description: '${json['description']}',
    target: json['target'] as String?,
    kpiName: '${json['kpi_name']}',
    unit: '${json['unit']}',
    baselinePeriod: json['baseline_period'] as String?,
    baselineValue: json['baseline_value'] == null
        ? null
        : '${json['baseline_value']}',
    expectedImpactValue: json['expected_impact_value'] == null
        ? null
        : '${json['expected_impact_value']}',
    acceptedBy: _person(json['accepted_by']),
    acceptedAt: '${json['accepted_at']}',
  );

  final String title;
  final String description;
  final String? target;
  final String kpiName;
  final String unit;
  final String? baselinePeriod;
  final String? baselineValue;
  final String? expectedImpactValue;
  final Person? acceptedBy;
  final String acceptedAt;
}

class FollowUp {
  const FollowUp({
    required this.dueDate,
    required this.measureMonth,
    required this.status,
    required this.isDue,
  });

  factory FollowUp.fromJson(Map<String, dynamic> json) => FollowUp(
    dueDate: '${json['due_date']}',
    measureMonth: '${json['measure_month']}',
    status: '${json['status']}',
    isDue: json['is_due'] == true,
  );

  final String dueDate;
  final String measureMonth;

  /// scheduled or done
  final String status;
  final bool isDue;
}

class Outcome {
  const Outcome({
    required this.outcome,
    required this.label,
    required this.reason,
    required this.kpiName,
    required this.unit,
    required this.baselineValue,
    required this.measuredValue,
    required this.achievedPct,
    required this.alternative,
  });

  factory Outcome.fromJson(Map<String, dynamic> json) => Outcome(
    outcome: '${json['outcome']}',
    label: '${json['label']}',
    reason: '${json['reason']}',
    kpiName: '${json['kpi_name']}',
    unit: '${json['unit']}',
    baselineValue: json['baseline_value'] == null
        ? null
        : '${json['baseline_value']}',
    measuredValue: json['measured_value'] == null
        ? null
        : '${json['measured_value']}',
    achievedPct: json['achieved_pct'] as int?,
    alternative: json['alternative'] as String?,
  );

  final String outcome;
  final String label;
  final String reason;
  final String kpiName;
  final String unit;
  final String? baselineValue;
  final String? measuredValue;
  final int? achievedPct;
  final String? alternative;
}

class ActionDetail {
  const ActionDetail({
    required this.id,
    required this.title,
    required this.description,
    required this.status,
    required this.statusLabel,
    required this.nextStatuses,
    required this.owner,
    required this.startDate,
    required this.targetDate,
    required this.daysLate,
    required this.steps,
    required this.progress,
    required this.decision,
    required this.updates,
    required this.followUp,
    required this.outcome,
  });

  factory ActionDetail.fromJson(Map<String, dynamic> json) => ActionDetail(
    id: '${json['id']}',
    title: '${json['title']}',
    description: '${json['description']}',
    status: '${json['status']}',
    statusLabel: '${json['status_label']}',
    nextStatuses: [
      for (final s in (json['next_statuses'] as List? ?? const [])) '$s',
    ],
    owner: _person(json['owner']),
    startDate: json['start_date'] as String?,
    targetDate: json['target_date'] as String?,
    daysLate: (json['days_late'] as int?) ?? 0,
    steps: [
      for (final s in (json['steps'] as List? ?? const []))
        ActionStep.fromJson(s as Map<String, dynamic>),
    ],
    progress: Progress.fromJson(
      (json['progress'] as Map<String, dynamic>?) ?? const {},
    ),
    decision: Decision.fromJson(json['decision'] as Map<String, dynamic>),
    updates: [
      for (final u in (json['updates'] as List? ?? const []))
        ActionUpdate.fromJson(u as Map<String, dynamic>),
    ],
    followUp: json['follow_up'] is Map<String, dynamic>
        ? FollowUp.fromJson(json['follow_up'] as Map<String, dynamic>)
        : null,
    outcome: json['outcome'] is Map<String, dynamic>
        ? Outcome.fromJson(json['outcome'] as Map<String, dynamic>)
        : null,
  );

  final String id;
  final String title;
  final String description;
  final String status;
  final String statusLabel;

  /// What it can be moved to from where it is.
  final List<String> nextStatuses;
  final Person? owner;
  final String? startDate;
  final String? targetDate;
  final int daysLate;
  final List<ActionStep> steps;
  final Progress progress;
  final Decision decision;

  /// Oldest first.
  final List<ActionUpdate> updates;
  final FollowUp? followUp;
  final Outcome? outcome;
}

class ActionCounts {
  const ActionCounts({
    required this.open,
    required this.overdue,
    required this.dueSoon,
    required this.mineOpen,
    required this.pending,
  });

  factory ActionCounts.fromJson(Map<String, dynamic> json) => ActionCounts(
    open: (json['open'] as int?) ?? 0,
    overdue: (json['overdue'] as int?) ?? 0,
    dueSoon: (json['due_soon'] as int?) ?? 0,
    mineOpen: (json['mine_open'] as int?) ?? 0,
    pending: (json['pending'] as int?) ?? 0,
  );

  final int open;
  final int overdue;
  final int dueSoon;
  final int mineOpen;
  final int pending;
}

class RecommendationSummary {
  const RecommendationSummary({
    required this.id,
    required this.eventId,
    required this.kpiName,
    required this.periodStart,
    required this.status,
    required this.headline,
    required this.recommended,
    required this.score,
  });

  factory RecommendationSummary.fromJson(Map<String, dynamic> json) =>
      RecommendationSummary(
        id: '${json['id']}',
        eventId: '${json['event_id']}',
        kpiName: '${json['kpi_name']}',
        periodStart: '${json['period_start']}',
        status: '${json['status']}',
        headline: '${json['headline']}',
        recommended: json['recommended'] as String?,
        score: json['score'] as int?,
      );

  final String id;
  final String eventId;
  final String kpiName;
  final String periodStart;

  /// open, no_action_needed, insufficient_evidence, dismissed, proposed or accepted
  final String status;
  final String headline;
  final String? recommended;
  final int? score;
}

class RecommendationOption {
  const RecommendationOption({
    required this.id,
    required this.rank,
    required this.isRecommended,
    required this.title,
    required this.description,
    required this.target,
    required this.impactValue,
    required this.impactUnit,
    required this.totalScore,
    required this.effort,
    required this.costLevel,
    required this.daysToEffect,
    required this.steps,
  });

  factory RecommendationOption.fromJson(Map<String, dynamic> json) {
    final intervention =
        (json['intervention'] as Map<String, dynamic>?) ?? const {};
    return RecommendationOption(
      id: '${json['id']}',
      rank: (json['rank'] as int?) ?? 0,
      isRecommended: json['is_recommended'] == true,
      title: '${json['title']}',
      description: '${json['description']}',
      target: json['target'] as String?,
      impactValue: '${json['impact_value']}',
      impactUnit: '${json['impact_unit']}',
      totalScore: (json['total_score'] as int?) ?? 0,
      effort: '${json['effort']}',
      costLevel: '${json['cost_level']}',
      daysToEffect: (json['days_to_effect'] as int?) ?? 0,
      steps: [
        for (final s in (intervention['steps'] as List? ?? const [])) '$s',
      ],
    );
  }

  final String id;
  final int rank;
  final bool isRecommended;
  final String title;
  final String description;
  final String? target;
  final String impactValue;
  final String impactUnit;
  final int totalScore;

  /// low, medium or high
  final String effort;

  /// none, low, medium or high
  final String costLevel;
  final int daysToEffect;
  final List<String> steps;
}

class RecommendationDetail {
  const RecommendationDetail({
    required this.status,
    required this.headline,
    required this.rationale,
    required this.options,
  });

  factory RecommendationDetail.fromJson(Map<String, dynamic> json) =>
      RecommendationDetail(
        status: '${json['status']}',
        headline: '${json['headline']}',
        rationale: '${json['rationale']}',
        options: [
          for (final o in (json['options'] as List? ?? const []))
            RecommendationOption.fromJson(o as Map<String, dynamic>),
        ],
      );

  final String status;
  final String headline;
  final String rationale;

  /// Best first.
  final List<RecommendationOption> options;
}
