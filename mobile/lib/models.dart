// What the server sends, as plain Dart objects. Only what the screens use is read.

import 'models_plain.dart';

class Business {
  const Business({
    required this.id,
    required this.name,
    required this.role,
    this.sells,
  });

  factory Business.fromJson(Map<String, dynamic> json) => Business(
    id: '${json['id']}',
    name: '${json['name']}',
    role: '${json['role']}',
    sells: json['sells'] as String?,
  );

  final String id;
  final String name;

  /// owner, manager or viewer
  final String role;

  /// products, services or both; null until the owner says
  final String? sells;

  /// A business that sells only a service has no stock to show.
  bool get hasStock => sells != 'services';

  /// Customers to follow up are for anyone who sells a service, or has not said.
  bool get showFollowUp => sells != 'products';
}

class CustomerToFollowUp {
  const CustomerToFollowUp({
    required this.name,
    required this.lastBoughtOn,
    required this.daysSince,
    required this.orders,
    required this.totalSpent,
  });

  factory CustomerToFollowUp.fromJson(Map<String, dynamic> json) =>
      CustomerToFollowUp(
        name: '${json['name']}',
        lastBoughtOn: '${json['last_bought_on']}',
        daysSince: (json['days_since'] as num).toInt(),
        orders: (json['orders'] as num).toInt(),
        totalSpent: '${json['total_spent']}',
      );

  final String name;
  final String lastBoughtOn;
  final int daysSince;
  final int orders;
  final String totalSpent;
}

class QuietCustomers {
  const QuietCustomers({
    required this.quietDays,
    required this.total,
    required this.customers,
  });

  factory QuietCustomers.fromJson(Map<String, dynamic> json) => QuietCustomers(
    quietDays: (json['quiet_days'] as num).toInt(),
    total: (json['total'] as num).toInt(),
    customers: [
      for (final c in (json['customers'] as List? ?? const []))
        CustomerToFollowUp.fromJson(c as Map<String, dynamic>),
    ],
  );

  final int quietDays;
  final int total;
  final List<CustomerToFollowUp> customers;
}

class AttentionItem {
  const AttentionItem({
    this.id = '',
    required this.kind,
    required this.severity,
    required this.title,
    required this.detail,
    required this.link,
    required this.canAct,
  });

  factory AttentionItem.fromJson(Map<String, dynamic> json) => AttentionItem(
    id: '${json['id'] ?? ''}',
    kind: '${json['kind']}',
    severity: '${json['severity']}',
    title: '${json['title']}',
    detail: '${json['detail']}',
    link: '${json['link']}',
    canAct: json['can_act'] == true,
  );

  /// Like `alert:` followed by the alert's id, or `action:` and the action's id.
  final String id;
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
    this.plain,
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
    plain: PlainFigure.from(json['plain']),
  );

  /// What it means, in everyday words (null when there is no number).
  final PlainFigure? plain;

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

/// One line under "What is going well?" or "What am I waiting for?".
class GlanceItem {
  const GlanceItem({
    required this.id,
    required this.title,
    required this.detail,
    required this.link,
    required this.tone,
  });

  factory GlanceItem.fromJson(Map<String, dynamic> json) => GlanceItem(
    id: '${json['id']}',
    title: '${json['title']}',
    detail: '${json['detail']}',
    link: '${json['link']}',
    tone: '${json['tone']}',
  );

  final String id;
  final String title;
  final String detail;

  /// The page it opens on the website, such as `actions.html#<id>`.
  final String link;

  /// good, bad, watch or neutral
  final String tone;
}

class ResultItem {
  const ResultItem({
    required this.title,
    required this.outcome,
    required this.outcomeLabel,
    required this.reason,
    required this.link,
  });

  factory ResultItem.fromJson(Map<String, dynamic> json) => ResultItem(
    title: '${json['title']}',
    outcome: '${json['outcome']}',
    outcomeLabel: '${json['outcome_label']}',
    reason: '${json['reason']}',
    link: '${json['link']}',
  );

  final String title;

  /// successful, partially_successful, unsuccessful or inconclusive
  final String outcome;
  final String outcomeLabel;
  final String reason;
  final String link;
}

/// "Did my actions help?"
class ResultsGlance {
  const ResultsGlance({
    required this.measured,
    required this.sentence,
    required this.latest,
    this.successful = 0,
    this.partly = 0,
    this.failed = 0,
    this.unclear = 0,
  });

  factory ResultsGlance.fromJson(Map<String, dynamic> json) => ResultsGlance(
    measured: (json['measured'] as num?)?.toInt() ?? 0,
    successful: (json['successful'] as num?)?.toInt() ?? 0,
    partly: (json['partially_successful'] as num?)?.toInt() ?? 0,
    failed: (json['unsuccessful'] as num?)?.toInt() ?? 0,
    unclear: (json['inconclusive'] as num?)?.toInt() ?? 0,
    sentence: '${json['sentence']}',
    latest: [
      for (final i in (json['latest'] as List? ?? const []))
        ResultItem.fromJson(i as Map<String, dynamic>),
    ],
  );

  final int measured;
  final String sentence;
  final List<ResultItem> latest;

  /// How many actions worked, partly worked, did not work, or cannot be measured yet.
  final int successful;
  final int partly;
  final int failed;
  final int unclear;
}

class Dashboard {
  const Dashboard({
    this.goingWell = const [],
    this.doFirst,
    this.waitingFor = const [],
    this.results,
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
    goingWell: [
      for (final i in (json['going_well'] as List? ?? const []))
        GlanceItem.fromJson(i as Map<String, dynamic>),
    ],
    doFirst: json['do_first'] is Map<String, dynamic>
        ? AttentionItem.fromJson(json['do_first'] as Map<String, dynamic>)
        : null,
    waitingFor: [
      for (final i in (json['waiting_for'] as List? ?? const []))
        GlanceItem.fromJson(i as Map<String, dynamic>),
    ],
    results: json['results'] is Map<String, dynamic>
        ? ResultsGlance.fromJson(json['results'] as Map<String, dynamic>)
        : null,
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

  /// What is going well, what to do first, what I am waiting for, and did my actions help.
  final List<GlanceItem> goingWell;
  final AttentionItem? doFirst;
  final List<GlanceItem> waitingFor;
  final ResultsGlance? results;

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
    this.ruleCode = '',
    this.postponedUntil,
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
    ruleCode: '${json['rule_code'] ?? ''}',
    postponedUntil: json['postponed_until'] as String?,
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

  /// Which kind of alert it is; "change_..." ones are about a figure that moved.
  final String ruleCode;

  /// The day it comes back, while it is put off for later (null otherwise).
  final String? postponedUntil;

  /// Whether this is about a figure that changed, so it has a card to decide on.
  bool get isChange => ruleCode.startsWith('change_');

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

class ServiceRow {
  const ServiceRow({
    required this.service,
    required this.kept,
    required this.cancelled,
    required this.missed,
    required this.rebookedPct,
  });

  factory ServiceRow.fromJson(Map<String, dynamic> json) => ServiceRow(
    service: '${json['service']}',
    kept: (json['kept'] as num).toInt(),
    cancelled: (json['cancelled'] as num).toInt(),
    missed: (json['missed'] as num).toInt(),
    rebookedPct: json['rebooked_pct'] == null
        ? null
        : '${json['rebooked_pct']}',
  );

  final String service;
  final int kept;
  final int cancelled;
  final int missed;
  final String? rebookedPct;
}

class TeamMember {
  const TeamMember({
    required this.staff,
    required this.kept,
    required this.cancelled,
    required this.missed,
    required this.sharePct,
    required this.averageRating,
  });

  factory TeamMember.fromJson(Map<String, dynamic> json) => TeamMember(
    staff: '${json['staff']}',
    kept: (json['kept'] as num).toInt(),
    cancelled: (json['cancelled'] as num).toInt(),
    missed: (json['missed'] as num).toInt(),
    sharePct: json['share_pct'] == null ? null : '${json['share_pct']}',
    averageRating: json['average_rating'] == null
        ? null
        : '${json['average_rating']}',
  );

  final String staff;
  final int kept;
  final int cancelled;
  final int missed;
  final String? sharePct;
  final String? averageRating;
}

/// Who did the appointments over the last three months, with plain notes about it.
class TeamWorkload {
  const TeamWorkload({required this.members, required this.notes});

  factory TeamWorkload.fromJson(Map<String, dynamic> json) => TeamWorkload(
    members: [
      for (final m in (json['members'] as List? ?? const []))
        TeamMember.fromJson(m as Map<String, dynamic>),
    ],
    notes: [for (final n in (json['notes'] as List? ?? const [])) '$n'],
  );

  final List<TeamMember> members;
  final List<String> notes;
}

/// The services people book, most popular first, and plain notes about them.
class ServicesBooked {
  const ServicesBooked({required this.rows, required this.notes});

  factory ServicesBooked.fromJson(Map<String, dynamic> json) => ServicesBooked(
    rows: [
      for (final r in (json['rows'] as List? ?? const []))
        ServiceRow.fromJson(r as Map<String, dynamic>),
    ],
    notes: [for (final n in (json['notes'] as List? ?? const [])) '$n'],
  );

  final List<ServiceRow> rows;
  final List<String> notes;
}
