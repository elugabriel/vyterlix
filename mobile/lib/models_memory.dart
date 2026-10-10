class Limits {
  const Limits({
    required this.maxCostLevel,
    required this.maxEffort,
    required this.excluded,
    required this.quickResultsOnly,
  });

  factory Limits.fromJson(Map<String, dynamic> json) => Limits(
    maxCostLevel: json['max_cost_level'] as String?,
    maxEffort: json['max_effort'] as String?,
    excluded: {for (final c in json['excluded_actions'] as List? ?? []) '$c'},
    quickResultsOnly: json['quick_results_only'] == true,
  );

  final String? maxCostLevel;
  final String? maxEffort;
  final Set<String> excluded;
  final bool quickResultsOnly;
}

class Lesson {
  const Lesson({required this.outcome, required this.lesson});

  factory Lesson.fromJson(Map<String, dynamic> json) =>
      Lesson(outcome: '${json['outcome']}', lesson: '${json['lesson']}');

  final String outcome;
  final String lesson;

  String get outcomeText =>
      const {
        'successful': 'It worked',
        'partially_successful': 'It partly worked',
        'unsuccessful': 'It did not work',
        'inconclusive': 'We cannot tell',
      }[outcome] ??
      outcome;
}

class ActionPattern {
  const ActionPattern({
    required this.action,
    required this.kpiName,
    required this.worked,
    required this.partly,
    required this.didNot,
    required this.couldNotTell,
    required this.achievedPct,
  });

  factory ActionPattern.fromJson(Map<String, dynamic> json) => ActionPattern(
    action: '${json['action']}',
    kpiName: '${json['kpi_name']}',
    worked: (json['successful'] as num?)?.toInt() ?? 0,
    partly: (json['partially_successful'] as num?)?.toInt() ?? 0,
    didNot: (json['unsuccessful'] as num?)?.toInt() ?? 0,
    couldNotTell: (json['inconclusive'] as num?)?.toInt() ?? 0,
    achievedPct: json['average_achieved_pct'] as String?,
  );

  final String action;
  final String kpiName;
  final int worked;
  final int partly;
  final int didNot;
  final int couldNotTell;
  final String? achievedPct;

  String get summary {
    final parts = [
      'worked $worked',
      'partly $partly',
      'did not $didNot',
      'could not tell $couldNotTell',
    ];
    return '${parts.join(' · ')}${achievedPct == null ? '' : ' · usually $achievedPct% of what was expected'}';
  }
}

class MemoryRecall {
  const MemoryRecall({required this.createdAt, required this.used});

  factory MemoryRecall.fromJson(Map<String, dynamic> json) => MemoryRecall(
    createdAt: '${json['created_at']}',
    used: [
      for (final u in json['used'] as List? ?? [])
        '${(u as Map<String, dynamic>)['statement']}',
    ],
  );

  final String createdAt;
  final List<String> used;
}

/// Everything Vyterlix has learned about the business.
class Memory {
  const Memory({
    required this.normalRanges,
    required this.customerPatterns,
    required this.goals,
    required this.seasons,
    required this.limits,
    required this.lessons,
    required this.patterns,
    required this.recentUse,
    required this.lastRebuilt,
  });

  factory Memory.fromJson(Map<String, dynamic> json) {
    List<String> statements(String key) => [
      for (final i in json[key] as List? ?? [])
        '${(i as Map<String, dynamic>)['statement']}',
    ];
    return Memory(
      normalRanges: statements('normal_ranges'),
      customerPatterns: statements('customer_patterns'),
      goals: statements('goals'),
      seasons: statements('seasons'),
      limits: Limits.fromJson(json['constraints'] as Map<String, dynamic>),
      lessons: [
        for (final l in json['lessons'] as List? ?? [])
          Lesson.fromJson(l as Map<String, dynamic>),
      ],
      patterns: [
        for (final p in json['patterns'] as List? ?? [])
          ActionPattern.fromJson(p as Map<String, dynamic>),
      ],
      recentUse: [
        for (final r in json['recent_use'] as List? ?? [])
          MemoryRecall.fromJson(r as Map<String, dynamic>),
      ],
      lastRebuilt: json['last_rebuilt'] as String?,
    );
  }

  final List<String> normalRanges;
  final List<String> customerPatterns;
  final List<String> goals;
  final List<String> seasons;
  final Limits limits;
  final List<Lesson> lessons;
  final List<ActionPattern> patterns;
  final List<MemoryRecall> recentUse;
  final String? lastRebuilt;
}
