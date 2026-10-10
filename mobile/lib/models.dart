// What the server sends, as plain Dart objects. Only what the screens use is read.

class Business {
  const Business({required this.id, required this.name, required this.role});

  factory Business.fromJson(Map<String, dynamic> json) => Business(
    id: '${json['id']}',
    name: '${json['name']}',
    role: '${json['role']}',
  );

  final String id;
  final String name;

  /// owner, manager or viewer
  final String role;
}

class AttentionItem {
  const AttentionItem({
    required this.kind,
    required this.severity,
    required this.title,
    required this.detail,
    required this.link,
    required this.canAct,
  });

  factory AttentionItem.fromJson(Map<String, dynamic> json) => AttentionItem(
    kind: '${json['kind']}',
    severity: '${json['severity']}',
    title: '${json['title']}',
    detail: '${json['detail']}',
    link: '${json['link']}',
    canAct: json['can_act'] == true,
  );

  final String kind;

  /// info, low, medium, high or critical
  final String severity;
  final String title;
  final String detail;
  final String link;
  final bool canAct;
}

class HealthGlance {
  const HealthGlance({
    required this.period,
    required this.score,
    required this.status,
    required this.previousScore,
    required this.weakest,
  });

  factory HealthGlance.fromJson(Map<String, dynamic> json) => HealthGlance(
    period: '${json['period']}',
    score: json['score'] as int?,
    status: '${json['status']}',
    previousScore: json['previous_score'] as int?,
    weakest: json['weakest'] as String?,
  );

  final String period;
  final int? score;
  final String status;
  final int? previousScore;
  final String? weakest;
}

class FigureGlance {
  const FigureGlance({
    required this.code,
    required this.name,
    required this.unit,
    required this.period,
    required this.value,
    required this.changePct,
    required this.direction,
  });

  factory FigureGlance.fromJson(Map<String, dynamic> json) => FigureGlance(
    code: '${json['code']}',
    name: '${json['name']}',
    unit: '${json['unit']}',
    period: '${json['period']}',
    value: json['value'] == null ? null : '${json['value']}',
    changePct: json['change_pct'] == null
        ? null
        : double.tryParse('${json['change_pct']}'),
    direction: '${json['direction']}',
  );

  final String code;
  final String name;

  /// gbp, percent, count or ratio
  final String unit;

  /// The first day of the month it is for, "2026-09-01".
  final String period;
  final String? value;
  final double? changePct;

  /// up_good, down_good or neutral
  final String direction;

  /// Whether the change is good news (null when there is nothing to say).
  bool? get isGood {
    final change = changePct;
    if (change == null || direction == 'neutral') return null;
    return (change >= 0) == (direction == 'up_good');
  }
}

class SetupGlance {
  const SetupGlance({required this.done, required this.total});

  factory SetupGlance.fromJson(Map<String, dynamic> json) =>
      SetupGlance(done: json['done'] as int, total: json['total'] as int);

  final int done;
  final int total;
}

class Dashboard {
  const Dashboard({
    required this.role,
    required this.headline,
    required this.attention,
    required this.moreAttention,
    required this.health,
    required this.figures,
    required this.setup,
    required this.unreadNotifications,
    required this.canAct,
  });

  factory Dashboard.fromJson(Map<String, dynamic> json) => Dashboard(
    role: '${json['role']}',
    headline: '${json['headline']}',
    attention: [
      for (final item in (json['attention'] as List? ?? const []))
        AttentionItem.fromJson(item as Map<String, dynamic>),
    ],
    moreAttention: (json['more_attention'] as int?) ?? 0,
    health: json['health'] == null
        ? null
        : HealthGlance.fromJson(json['health'] as Map<String, dynamic>),
    figures: [
      for (final item in (json['figures'] as List? ?? const []))
        FigureGlance.fromJson(item as Map<String, dynamic>),
    ],
    setup: json['setup'] == null
        ? null
        : SetupGlance.fromJson(json['setup'] as Map<String, dynamic>),
    unreadNotifications: (json['unread_notifications'] as int?) ?? 0,
    canAct: json['can_act'] == true,
  );

  final String role;
  final String headline;
  final List<AttentionItem> attention;
  final int moreAttention;
  final HealthGlance? health;
  final List<FigureGlance> figures;
  final SetupGlance? setup;
  final int unreadNotifications;
  final bool canAct;
}
