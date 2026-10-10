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

class AlertSummary {
  const AlertSummary({
    required this.id,
    required this.title,
    required this.body,
    required this.severity,
    required this.status,
    required this.occurrences,
    required this.firstSeenAt,
    required this.lastSeenAt,
    required this.link,
    required this.acknowledgedBy,
    required this.resolvedAt,
  });

  factory AlertSummary.fromJson(Map<String, dynamic> json) => AlertSummary(
    id: '${json['id']}',
    title: '${json['title']}',
    body: '${json['body']}',
    severity: '${json['severity']}',
    status: '${json['status']}',
    occurrences: (json['occurrences'] as int?) ?? 1,
    firstSeenAt: '${json['first_seen_at']}',
    lastSeenAt: '${json['last_seen_at']}',
    link: json['link'] as String?,
    acknowledgedBy: json['acknowledged_by'] as String?,
    resolvedAt: json['resolved_at'] as String?,
  );

  final String id;
  final String title;
  final String body;

  /// info, low, medium, high or critical
  final String severity;

  /// open, acknowledged or resolved
  final String status;
  final int occurrences;
  final String firstSeenAt;
  final String lastSeenAt;
  final String? link;
  final String? acknowledgedBy;
  final String? resolvedAt;
}

class AlertEvent {
  const AlertEvent({
    required this.kind,
    required this.user,
    required this.note,
    required this.createdAt,
  });

  factory AlertEvent.fromJson(Map<String, dynamic> json) => AlertEvent(
    kind: '${json['kind']}',
    user: json['user'] as String?,
    note: json['note'] as String?,
    createdAt: '${json['created_at']}',
  );

  /// raised, repeated, acknowledged, resolved or reopened
  final String kind;

  /// Nobody when the system did it.
  final String? user;
  final String? note;
  final String createdAt;
}

class AlertDetail {
  const AlertDetail({required this.alert, required this.events});

  factory AlertDetail.fromJson(Map<String, dynamic> json) => AlertDetail(
    alert: AlertSummary.fromJson(json),
    events: [
      for (final event in (json['events'] as List? ?? const []))
        AlertEvent.fromJson(event as Map<String, dynamic>),
    ],
  );

  final AlertSummary alert;
  final List<AlertEvent> events;
}

class AlertCounts {
  const AlertCounts({
    required this.open,
    required this.acknowledged,
    required this.resolved,
    required this.seriousOpen,
  });

  factory AlertCounts.fromJson(Map<String, dynamic> json) => AlertCounts(
    open: (json['open'] as int?) ?? 0,
    acknowledged: (json['acknowledged'] as int?) ?? 0,
    resolved: (json['resolved'] as int?) ?? 0,
    seriousOpen: (json['high_or_critical_open'] as int?) ?? 0,
  );

  final int open;
  final int acknowledged;
  final int resolved;
  final int seriousOpen;
}

class AppNotification {
  const AppNotification({
    required this.id,
    required this.title,
    required this.body,
    required this.severity,
    required this.read,
    required this.createdAt,
    required this.link,
  });

  factory AppNotification.fromJson(Map<String, dynamic> json) =>
      AppNotification(
        id: '${json['id']}',
        title: '${json['title']}',
        body: '${json['body']}',
        severity: '${json['severity']}',
        read: json['read'] == true,
        createdAt: '${json['created_at']}',
        link: json['link'] as String?,
      );

  final String id;
  final String title;
  final String body;
  final String severity;
  final bool read;
  final String createdAt;
  final String? link;

  AppNotification asRead() => AppNotification(
    id: id,
    title: title,
    body: body,
    severity: severity,
    read: true,
    createdAt: createdAt,
    link: link,
  );
}

class Inbox {
  const Inbox({required this.unread, required this.items});

  factory Inbox.fromJson(Map<String, dynamic> json) => Inbox(
    unread: (json['unread'] as int?) ?? 0,
    items: [
      for (final item in (json['items'] as List? ?? const []))
        AppNotification.fromJson(item as Map<String, dynamic>),
    ],
  );

  final int unread;
  final List<AppNotification> items;
}
