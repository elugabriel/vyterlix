import 'models_plain.dart';

/// An alert with everything needed to decide, as the server sends it (GET /alerts/{id}/card).

class CardPriority {
  const CardPriority({
    required this.level,
    required this.label,
    required this.reason,
  });

  factory CardPriority.fromJson(Map<String, dynamic> json) => CardPriority(
    level: '${json['level']}',
    label: '${json['label']}',
    reason: '${json['reason']}',
  );

  /// low, medium or high
  final String level;
  final String label;
  final String reason;
}

class CardChart {
  const CardChart({
    required this.name,
    required this.unit,
    required this.marked,
    required this.months,
    required this.values,
  });

  factory CardChart.fromJson(Map<String, dynamic> json) {
    final points = (json['points'] as List? ?? const [])
        .cast<Map<String, dynamic>>();
    return CardChart(
      name: '${json['name']}',
      unit: '${json['unit']}',
      marked: '${json['marked']}',
      months: [for (final p in points) '${p['period_start']}'],
      values: [
        for (final p in points)
          p['value'] == null ? null : double.tryParse('${p['value']}'),
      ],
    );
  }

  final String name;
  final String unit;

  /// The first day of the month the alert is about, "2026-03-01".
  final String marked;

  /// The first day of each month, oldest first, and the figure for each.
  final List<String> months;
  final List<double?> values;

  int get markedIndex => months.indexOf(marked);
}

class CardSuggestion {
  const CardSuggestion({
    required this.optionId,
    required this.title,
    required this.description,
    required this.steps,
    required this.whyThisOne,
    required this.effort,
    required this.costLevel,
    required this.daysToEffect,
    required this.dueBy,
    required this.otherOptions,
  });

  factory CardSuggestion.fromJson(Map<String, dynamic> json) => CardSuggestion(
    optionId: '${json['option_id']}',
    title: '${json['title']}',
    description: '${json['description']}',
    steps: [for (final s in json['steps'] as List? ?? const []) '$s'],
    whyThisOne: '${json['why_this_one']}',
    effort: '${json['effort']}',
    costLevel: '${json['cost_level']}',
    daysToEffect: (json['days_to_effect'] as num?)?.toInt() ?? 0,
    dueBy: '${json['due_by']}',
    otherOptions: (json['other_options'] as num?)?.toInt() ?? 0,
  );

  final String optionId;
  final String title;
  final String description;
  final List<String> steps;
  final String whyThisOne;
  final String effort;
  final String costLevel;
  final int daysToEffect;

  /// The date it would get if accepted as it is, "2026-10-29".
  final String dueBy;
  final int otherOptions;
}

class CardAction {
  const CardAction({
    required this.id,
    required this.title,
    required this.owner,
    required this.targetDate,
    required this.checkFrom,
  });

  factory CardAction.fromJson(Map<String, dynamic> json) => CardAction(
    id: '${json['id']}',
    title: '${json['title']}',
    owner: json['owner'] as String?,
    targetDate: json['target_date'] as String?,
    checkFrom: json['check_from'] as String?,
  );

  final String id;
  final String title;
  final String? owner;
  final String? targetDate;
  final String? checkFrom;
}

class AlertCard {
  const AlertCard({
    required this.state,
    required this.headline,
    required this.happened,
    required this.numbers,
    required this.whyItMatters,
    required this.seasonNote,
    required this.tone,
    required this.priority,
    required this.chart,
    required this.evidence,
    required this.suggestion,
    required this.action,
    required this.dismissedReason,
    required this.nextStepText,
    required this.canDecide,
  });

  factory AlertCard.fromJson(Map<String, dynamic> json) => AlertCard(
    state: '${json['state']}',
    headline: '${json['headline']}',
    happened: '${json['happened']}',
    numbers: json['numbers'] as String?,
    whyItMatters: json['why_it_matters'] as String?,
    seasonNote: json['season_note'] as String?,
    tone: '${json['tone']}',
    priority: CardPriority.fromJson(json['priority'] as Map<String, dynamic>),
    chart: json['chart'] is Map<String, dynamic>
        ? CardChart.fromJson(json['chart'] as Map<String, dynamic>)
        : null,
    evidence: [for (final e in json['evidence'] as List? ?? const []) '$e'],
    suggestion: json['suggestion'] is Map<String, dynamic>
        ? CardSuggestion.fromJson(json['suggestion'] as Map<String, dynamic>)
        : null,
    action: json['action'] is Map<String, dynamic>
        ? CardAction.fromJson(json['action'] as Map<String, dynamic>)
        : null,
    dismissedReason: json['dismissed_reason'] as String?,
    nextStepText: json['next_step'] is Map<String, dynamic>
        ? '${(json['next_step'] as Map<String, dynamic>)['text']}'
        : null,
    canDecide: json['can_decide'] == true,
  );

  /// open, proposed, accepted, dismissed, good_news, no_suggestion, not_yet or other.
  final String state;
  final String headline;
  final String happened;
  final String? numbers;
  final String? whyItMatters;
  final String? seasonNote;

  /// good, bad, watch, neutral or unknown
  final String tone;
  final CardPriority priority;
  final CardChart? chart;
  final List<String> evidence;
  final CardSuggestion? suggestion;
  final CardAction? action;
  final String? dismissedReason;
  final String? nextStepText;
  final bool canDecide;

  PlainFigure get plain => PlainFigure(
    says: happened,
    why: whyItMatters ?? '',
    tone: tone,
    caution: null,
  );
}
