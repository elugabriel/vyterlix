import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:vyterlix_mobile/api/api_client.dart';
import 'package:vyterlix_mobile/app.dart';
import 'package:vyterlix_mobile/auth/auth_controller.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';

const baseUrl = 'http://test.local/api/v1';

http.Response jsonResponse(Object body, [int status = 200]) => http.Response(
  jsonEncode(body),
  status,
  headers: {'content-type': 'application/json; charset=utf-8'},
);

http.Response errorResponse(int status, String code, String message) =>
    jsonResponse({
      'error': {
        'code': code,
        'message': message,
        'details': null,
        'request_id': 'r',
      },
    }, status);

/// A pretend Vyterlix server: answers by "METHOD /path" and remembers what it was asked.
class FakeServer {
  final routes = <String, http.Response Function(http.Request)>{};
  final requests = <http.Request>[];

  /// Set to make every request fail as if there were no connection.
  bool down = false;

  void on(String route, http.Response Function(http.Request) answer) =>
      routes[route] = answer;

  void json(String route, Object body, [int status = 200]) =>
      on(route, (_) => jsonResponse(body, status));

  List<http.Request> to(String route) => requests
      .where(
        (r) => '${r.method} ${r.url.path.replaceFirst('/api/v1', '')}' == route,
      )
      .toList();

  Map<String, dynamic> bodyOf(http.Request request) =>
      jsonDecode(request.body) as Map<String, dynamic>;

  MockClient get client => MockClient((request) async {
    requests.add(request);
    if (down) throw http.ClientException('no connection');
    final key =
        '${request.method} ${request.url.path.replaceFirst('/api/v1', '')}';
    final answer = routes[key];
    if (answer == null) {
      return errorResponse(404, 'not_found', 'No such route $key');
    }
    return answer(request);
  });
}

Map<String, dynamic> tokens({
  String access = 'access-1',
  String refresh = 'refresh-1',
}) => {
  'access_token': access,
  'token_type': 'bearer',
  'expires_in': 900,
  'refresh_token': refresh,
  'user': {
    'id': 'u1',
    'email': 'jo@example.co.uk',
    'full_name': 'Jo Baker',
    'email_verified': true,
  },
};

const businessList = [
  {'id': 'b1', 'name': 'Fakeham Bakery', 'role': 'owner'},
  {'id': 'b2', 'name': 'Second Shop', 'role': 'viewer'},
];

Map<String, dynamic> dashboard({
  bool health = true,
  int attention = 2,
  bool setup = false,
}) => {
  'role': 'owner',
  'headline': attention == 0
      ? 'Nothing needs your attention today.'
      : '$attention things need your attention today, 1 of them serious.',
  'attention': [
    for (var i = 0; i < attention; i++)
      {
        'id': 'a$i',
        'kind': i == 0 ? 'alert' : 'action_overdue',
        'severity': i == 0 ? 'high' : 'medium',
        'title': 'Item $i title',
        'detail': 'Item $i detail',
        'link': 'alerts.html',
        'category': null,
        'can_act': true,
      },
  ],
  'more_attention': 3,
  'health': health
      ? {
          'period': '2026-09-01',
          'score': 69,
          'status': 'fair',
          'previous_score': 89,
          'weakest': 'Sales',
          'explanation': 'x',
        }
      : null,
  'figures': [
    {
      'code': 'sales',
      'name': 'Sales',
      'unit': 'gbp',
      'period': '2026-09-01',
      'value': '8717.1',
      'change_pct': '9.1',
      'direction': 'up_good',
    },
    {
      'code': 'costs',
      'name': 'Running costs',
      'unit': 'gbp',
      'period': '2026-09-01',
      'value': '5439',
      'change_pct': '8.2',
      'direction': 'down_good',
    },
    {
      'code': 'margin',
      'name': 'Gross margin',
      'unit': 'percent',
      'period': '2026-09-01',
      'value': '75.6',
      'change_pct': null,
      'direction': 'up_good',
    },
  ],
  'setup': setup
      ? {'done': 3, 'total': 7, 'next_section': 'goals', 'ready': false}
      : null,
  'unread_notifications': 6,
  'open_actions': 1,
  'can_act': true,
};

Map<String, dynamic> alertJson(
  String id, {
  String severity = 'high',
  String status = 'open',
  int occurrences = 3,
}) => {
  'id': id,
  'rule_code': 'change_sales',
  'category': 'sales',
  'kpi_category': null,
  'severity': severity,
  'title': 'Alert $id title',
  'body': 'Alert $id body',
  'link': 'changes.html',
  'status': status,
  'occurrences': occurrences,
  'first_seen_at': '2026-10-01T09:30:00',
  'last_seen_at': '2026-10-09T13:05:00',
  'acknowledged_by': status == 'acknowledged' ? 'Jo Baker' : null,
  'acknowledged_at': null,
  'resolved_at': status == 'resolved' ? '2026-10-09T14:00:00' : null,
};

Map<String, dynamic> alertDetailJson(
  String id, {
  String status = 'open',
  List<Map<String, dynamic>>? events,
}) => {
  ...alertJson(id, status: status),
  'events':
      events ??
      [
        {
          'kind': 'raised',
          'user': null,
          'note': null,
          'created_at': '2026-10-01T09:30:00',
        },
        {
          'kind': 'repeated',
          'user': null,
          'note': null,
          'created_at': '2026-10-09T13:05:00',
        },
      ],
};

Map<String, dynamic> notificationJson(
  String id, {
  bool read = false,
  String severity = 'high',
}) => {
  'id': id,
  'alert_id': null,
  'category': 'sales',
  'severity': severity,
  'title': 'Notification $id',
  'body': 'Body of $id',
  'link': null,
  'read': read,
  'email': 'none',
  'created_at': '2026-10-09T13:05:00',
};

Map<String, dynamic> actionSummaryJson(
  String id, {
  String status = 'in_progress',
  String label = 'In progress',
  int daysLate = 0,
  bool dueSoon = false,
  Map<String, dynamic>? owner = const {'id': 'u1', 'name': 'Jo Baker'},
  String? target = '2026-10-31',
  int done = 1,
  int total = 3,
}) => {
  'id': id,
  'title': 'Action $id title',
  'category': 'sales',
  'kpi_name': 'Sales',
  'status': status,
  'status_label': label,
  'owner': owner,
  'start_date': '2026-10-01',
  'target_date': target,
  'days_late': daysLate,
  'due_soon': dueSoon,
  'progress': {
    'done': done,
    'total': total,
    'percent': total == 0 ? 0 : (done * 100 ~/ total),
  },
  'last_activity_at': '2026-10-09T13:05:00',
};

Map<String, dynamic> actionDetailJson(
  String id, {
  String status = 'in_progress',
  String label = 'In progress',
  List<String> next = const ['partially_completed', 'completed', 'cancelled'],
  int daysLate = 0,
  List<Map<String, dynamic>>? steps,
  Map<String, dynamic>? followUp,
  Map<String, dynamic>? outcome,
  List<Map<String, dynamic>>? updates,
}) {
  final stepList =
      steps ??
      [
        {'text': 'Ring the supplier', 'done': true},
        {'text': 'Change the order', 'done': false},
        {'text': 'Tell the team', 'done': false},
      ];
  final done = stepList.where((s) => s['done'] == true).length;
  return {
    'id': id,
    'title': 'Action $id title',
    'description': 'Action $id description',
    'category': 'sales',
    'status': status,
    'status_label': label,
    'next_statuses': next,
    'owner': {'id': 'u1', 'name': 'Jo Baker'},
    'created_by': {'id': 'u1', 'name': 'Jo Baker'},
    'approved_by': null,
    'start_date': '2026-10-01',
    'target_date': '2026-10-31',
    'completed_at': null,
    'days_late': daysLate,
    'due_soon': false,
    'steps': stepList,
    'progress': {
      'done': done,
      'total': stepList.length,
      'percent': stepList.isEmpty ? 0 : (done * 100 ~/ stepList.length),
    },
    'decision': {
      'title': 'Action $id title',
      'original_title': null,
      'modified': false,
      'description': 'Action $id description',
      'target': 'Sourdough',
      'library_code': 'x',
      'kpi_code': 'sales',
      'kpi_name': 'Sales',
      'unit': 'gbp',
      'baseline_period': '2026-09-01',
      'baseline_value': '8717.1',
      'expected_impact_value': '450',
      'expected_impact_unit': 'gbp',
      'recommendation_score': 80,
      'accepted_by': {'id': 'u1', 'name': 'Jo Baker'},
      'accepted_at': '2026-10-01T09:00:00',
      'event_id': 'ev1',
    },
    'updates':
        updates ??
        [
          {
            'id': 'up1',
            'kind': 'created',
            'from_status': null,
            'to_status': 'accepted',
            'note': null,
            'details': {},
            'user': {'id': 'u1', 'name': 'Jo Baker'},
            'created_at': '2026-10-01T09:00:00',
          },
          {
            'id': 'up2',
            'kind': 'overdue',
            'from_status': 'accepted',
            'to_status': 'overdue',
            'note': 'Past its date',
            'details': {},
            'user': null,
            'created_at': '2026-10-08T07:00:00',
          },
        ],
    'evidence': [],
    'follow_up': followUp,
    'outcome': outcome,
    'last_activity_at': '2026-10-09T13:05:00',
  };
}

Map<String, dynamic> optionJson(
  String id,
  int rank, {
  bool recommended = false,
  String title = 'Option',
}) => {
  'id': id,
  'rank': rank,
  'is_recommended': recommended,
  'title': '$title $id',
  'description': 'Description of $id',
  'target': recommended ? 'Sourdough' : null,
  'intervention': {
    'code': 'c$id',
    'name': 'Intervention $id',
    'category': 'sales',
    'summary': 's',
    'steps': ['First step of $id', 'Second step of $id'],
    'effort': 'low',
    'cost_level': 'none',
    'typical_days_to_effect': 14,
    'impact_share': '0.3',
    'impact_basis': 'b',
  },
  'impact_value': '450',
  'impact_unit': 'gbp',
  'total_score': recommended ? 82 : 61,
  'scores': [],
  'effort': recommended ? 'low' : 'high',
  'cost_level': recommended ? 'none' : 'medium',
  'days_to_effect': recommended ? 14 : 30,
};

Map<String, dynamic> recommendationJson({
  String status = 'open',
  bool options = true,
}) => {
  'id': 'rec1',
  'event': {},
  'status': status,
  'headline': 'Sales fell 11.9% in September',
  'rationale': 'The best fit is to re-price the weakest line.',
  'rules_version': '1',
  'generated_at': '2026-10-09T10:00:00',
  'options': options
      ? [
          optionJson('o1', 1, recommended: true, title: 'Re-price'),
          optionJson('o2', 2, title: 'Promote'),
        ]
      : [],
  'evidence': [],
};

Map<String, dynamic> kpiValueJson({
  String start = '2026-09-01',
  bool complete = true,
  String status = 'ok',
  String? value = '8717.10',
  String? previous = '7990.00',
  String? pct = '9.1',
}) => {
  'period_start': start,
  'period_end': start,
  'is_complete': complete,
  'status': status,
  'value': value,
  'previous_value': previous,
  'change_pct': pct,
  'data_quality': 95,
  'inputs': {},
};

Map<String, dynamic> kpiJson(
  String code,
  String name,
  String category, {
  String unit = 'gbp',
  String direction = 'up_good',
  Map<String, dynamic>? latest,
  Map<String, dynamic>? current,
  List<String> requires = const ['sales'],
}) => {
  'code': code,
  'name': name,
  'description': 'About $name',
  'category': category,
  'unit': unit,
  'direction': direction,
  'requires': requires,
  'latest': latest,
  'current': current,
};

Map<String, dynamic> kpisJson() => {
  'granularity': 'month',
  'last_run': {'finished_at': '2026-10-09T10:47:00'},
  'kpis': [
    kpiJson(
      'sales',
      'Sales',
      'sales',
      latest: kpiValueJson(),
      current: kpiValueJson(
        start: '2026-10-01',
        complete: false,
        value: '1200.00',
      ),
    ),
    kpiJson(
      'costs',
      'Running costs',
      'financial',
      direction: 'down_good',
      latest: kpiValueJson(value: '5439.00', previous: '5000.00', pct: '8.8'),
      requires: ['expenses'],
    ),
    kpiJson(
      'margin',
      'Gross margin',
      'financial',
      unit: 'percent',
      latest: kpiValueJson(value: '75.6', previous: '70.0', pct: '8.0'),
    ),
    kpiJson(
      'profit',
      'Profit',
      'financial',
      latest: kpiValueJson(
        status: 'no_data',
        value: null,
        previous: null,
        pct: null,
      ),
      requires: ['sales', 'expenses'],
    ),
    kpiJson(
      'repeat',
      'Repeat customers',
      'customer',
      unit: 'ratio',
      latest: null,
    ),
  ],
};

Map<String, dynamic> kpiHistoryJson() => {
  'code': 'sales',
  'name': 'Sales',
  'description': 'About Sales',
  'category': 'sales',
  'unit': 'gbp',
  'direction': 'up_good',
  'requires': ['sales'],
  'granularity': 'month',
  'formula': 'Sales: the money taken, net of VAT, after refunds.',
  'values': [
    kpiValueJson(
      start: '2026-07-01',
      value: '7000.00',
      previous: null,
      pct: null,
    ),
    kpiValueJson(
      start: '2026-08-01',
      value: '7990.00',
      previous: '7000.00',
      pct: '14.1',
    ),
    kpiValueJson(start: '2026-09-01'),
    kpiValueJson(
      start: '2026-10-01',
      complete: false,
      value: '1200.00',
      previous: '8717.10',
      pct: '-86.2',
    ),
  ],
};

Map<String, dynamic> healthJson() => {
  'period_start': '2026-09-01',
  'period_end': '2026-09-30',
  'overall_score': 69,
  'status': 'fair',
  'previous_score': 89,
  'trend': 'down',
  'coverage_pct': 80,
  'data_quality': 95,
  'explanation':
      'Your business is doing fairly well; sales are holding it back.',
  'calculated_at': '2026-10-09T10:47:00',
  'components': [
    {
      'category': 'financial',
      'label': 'Money',
      'score': 82,
      'status': 'healthy',
      'previous_score': 80,
      'trend': 'up',
      'weight': 0.5,
      'contribution': 41.0,
      'explanation': 'Money is in good shape.',
      'metrics': [
        {
          'kpi_code': 'margin',
          'name': 'Gross margin',
          'unit': 'percent',
          'basis': 'value',
          'value': '75.6',
          'baseline': null,
          'baseline_months': 0,
          'compared_pct': null,
          'score': 90.0,
          'weight': 1.0,
          'text': 'Margin was 75.6%, which scored 90.',
        },
      ],
    },
    {
      'category': 'sales',
      'label': 'Sales',
      'score': 40,
      'status': 'at_risk',
      'previous_score': 70,
      'trend': 'down',
      'weight': 0.5,
      'contribution': 20.0,
      'explanation': 'Sales fell a lot.',
      'metrics': [
        {
          'kpi_code': 'sales',
          'name': 'Sales growth',
          'unit': 'percent',
          'basis': 'vs_baseline',
          'value': '-11.9',
          'baseline': '-0.7',
          'baseline_months': 6,
          'compared_pct': '-11.2',
          'score': 30.0,
          'weight': 1.0,
          'text':
              'Sales growth was -11.9% against a usual -0.7%, which scored 30.',
        },
      ],
    },
  ],
};

Map<String, dynamic> healthHistoryJson() => {
  'points': [
    {
      'period_start': '2026-06-01',
      'overall_score': 85,
      'status': 'healthy',
      'trend': null,
      'coverage_pct': 100,
    },
    {
      'period_start': '2026-07-01',
      'overall_score': 88,
      'status': 'healthy',
      'trend': 'up',
      'coverage_pct': 100,
    },
    {
      'period_start': '2026-08-01',
      'overall_score': 89,
      'status': 'healthy',
      'trend': 'up',
      'coverage_pct': 100,
    },
    {
      'period_start': '2026-09-01',
      'overall_score': 69,
      'status': 'fair',
      'trend': 'down',
      'coverage_pct': 80,
    },
  ],
};

Map<String, dynamic> forecastJson({
  String status = 'ok',
  String name = 'Sales',
}) => {
  'id': 'f1',
  'kpi_code': 'sales',
  'kpi_name': name,
  'unit': 'gbp',
  'status': status,
  'as_of': '2026-09-01',
  'horizon': 3,
  'interval_level': 80,
  'history_months': 4,
  'adjusted_for_seasons': false,
  'method': {
    'code': 'recent_average',
    'name': 'Recent average',
    'description': 'd',
    'version': '1',
  },
  'explanation': status == 'ok'
      ? 'We expect $name of about £9,117.97 in October.'
      : 'There is not enough history to forecast yet.',
  'calculated_at': '2026-10-09T10:00:00',
  'predictions': status == 'ok'
      ? [
          {
            'period_start': '2026-10-01',
            'period_end': '2026-10-31',
            'value': '9117.97',
            'lower': '7836.51',
            'upper': '10399.44',
            'actual_value': null,
          },
          {
            'period_start': '2026-11-01',
            'period_end': '2026-11-30',
            'value': '9200.00',
            'lower': '7305.71',
            'upper': '10930.24',
            'actual_value': '9400.00',
          },
          {
            'period_start': '2026-12-01',
            'period_end': '2026-12-31',
            'value': '12765.16',
            'lower': '9657.77',
            'upper': '15872.55',
            'actual_value': null,
          },
        ]
      : [],
  'history': [
    {'period_start': '2026-06-01', 'value': '7000.00'},
    {'period_start': '2026-07-01', 'value': '7990.00'},
    {'period_start': '2026-08-01', 'value': '8717.10'},
    {'period_start': '2026-09-01', 'value': '8500.00'},
  ],
  'evaluations': [],
};

Map<String, dynamic> stockJson() => {
  'month': '2026-10-01',
  'as_of': '2026-09-01',
  'level': 80,
  'order_now': 1,
  'watch': 1,
  'ok': 1,
  'not_enough_history': 0,
  'headline': '1 product to order now, 1 to keep an eye on.',
  'note': 'Based on what each product has sold in recent months.',
  'rows': [
    {
      'product_id': 'p1',
      'name': 'Sourdough loaf',
      'sku': null,
      'expected_units': 300,
      'lower_units': 250,
      'upper_units': 360,
      'on_hand': 40,
      'days_of_cover': 4,
      'status': 'order_now',
      'order_suggested': 320,
      'method': 'm',
      'history_months': 6,
      'typical_miss_pct': '8.0',
    },
    {
      'product_id': 'p2',
      'name': 'Rye loaf',
      'sku': null,
      'expected_units': 100,
      'lower_units': 80,
      'upper_units': 130,
      'on_hand': 90,
      'days_of_cover': 27,
      'status': 'ok',
      'order_suggested': 130,
      'method': 'm',
      'history_months': 6,
      'typical_miss_pct': '9.0',
    },
  ],
};

Map<String, dynamic> changeJson(
  String id, {
  String effect = 'bad',
  String severity = 'major',
  bool season = false,
}) => {
  'id': id,
  'kpi_code': 'sales',
  'kpi_name': 'Sales',
  'category': 'sales',
  'unit': 'gbp',
  'kind': 'material_change',
  'period_start': '2026-09-01',
  'period_end': '2026-09-30',
  'direction': effect == 'bad' ? 'down' : 'up',
  'severity': severity,
  'effect': effect,
  'value': '8000',
  'reference_value': '9000',
  'change': '-11.1',
  'change_unit': 'percent',
  'explained_by_season': season,
  'data_quality': 95,
  'summary': 'Change $id summary',
  'status': 'open',
  'detected_at': '2026-10-01T07:00:00',
};

Map<String, dynamic> diagnosisJson() => {
  'id': 'd1',
  'event': changeJson('ch1'),
  'status': 'ready',
  'headline': 'Fewer people came in on weekdays',
  'summary': 'Most of the fall came from Tuesdays and Wednesdays.',
  'confidence': 72,
  'confidence_label': 'medium',
  'confidence_note': 'Worked out from sales alone.',
  'rules_version': '1',
  'diagnosed_at': '2026-10-02T07:00:00',
  'evidence': [
    {
      'id': 'e1',
      'evidence_type': 'fact',
      'statement': 'There were 12 fewer sales on Tuesdays.',
      'data': {},
      'sort_order': 1,
    },
    {
      'id': 'e2',
      'evidence_type': 'statistical',
      'statement': 'The fall is bigger than usual.',
      'data': {},
      'sort_order': 2,
    },
  ],
};

/// A server that has a signed-in person, two businesses and a dashboard.
FakeServer happyServer() {
  final server = FakeServer()
    ..json('POST /auth/login', tokens())
    ..json(
      'POST /auth/refresh',
      tokens(access: 'access-2', refresh: 'refresh-2'),
    )
    ..on('POST /auth/logout', (_) => http.Response('', 204))
    ..json('POST /auth/forgot-password', {
      'message': 'If an account exists for that email, we have sent a link.',
    }, 202)
    ..json('GET /organizations', businessList)
    ..json('GET /organizations/b1/dashboard', dashboard())
    ..json(
      'GET /organizations/b2/dashboard',
      dashboard(health: false, attention: 0),
    )
    ..on('GET /organizations/b1/alerts', (request) {
      final status = request.url.queryParameters['status'];
      return jsonResponse(switch (status) {
        'open' => [
          alertJson('al1'),
          alertJson('al2', severity: 'medium', occurrences: 1),
        ],
        'resolved' => [alertJson('al3', status: 'resolved', severity: 'low')],
        _ => [],
      });
    })
    ..json('GET /organizations/b1/alerts/summary', {
      'open': 2,
      'acknowledged': 0,
      'resolved': 1,
      'high_or_critical_open': 1,
    })
    ..json('GET /organizations/b1/alerts/al1', alertDetailJson('al1'))
    ..json(
      'POST /organizations/b1/alerts/al1/acknowledge',
      alertDetailJson(
        'al1',
        status: 'acknowledged',
        events: [
          {
            'kind': 'raised',
            'user': null,
            'note': null,
            'created_at': '2026-10-01T09:30:00',
          },
          {
            'kind': 'acknowledged',
            'user': 'Jo Baker',
            'note': 'Ringing the supplier',
            'created_at': '2026-10-09T15:00:00',
          },
        ],
      ),
    )
    ..json(
      'POST /organizations/b1/alerts/al1/resolve',
      alertDetailJson('al1', status: 'resolved'),
    )
    ..json('GET /organizations/b2/alerts', [alertJson('al9')])
    ..json('GET /organizations/b2/alerts/summary', {
      'open': 1,
      'acknowledged': 0,
      'resolved': 0,
      'high_or_critical_open': 1,
    })
    ..json('GET /organizations/b2/alerts/al9', alertDetailJson('al9'))
    ..json('GET /organizations/b1/notifications', {
      'unread': 2,
      'items': [
        notificationJson('n1'),
        notificationJson('n2', severity: 'medium'),
        notificationJson('n3', read: true),
      ],
    })
    ..on(
      'POST /organizations/b1/notifications/read-all',
      (_) => http.Response('', 204),
    )
    ..on(
      'POST /organizations/b1/notifications/n1/read',
      (_) => http.Response('', 204),
    )
    ..on(
      'POST /organizations/b1/notifications/n2/read',
      (_) => http.Response('', 204),
    )
    ..json('GET /organizations/b2/notifications', {'unread': 0, 'items': []})
    ..on('GET /organizations/b1/actions', (request) {
      final q = request.url.queryParameters;
      if (q['status'] == 'pending') {
        return jsonResponse([
          actionSummaryJson(
            'a2',
            status: 'pending',
            label: 'Waiting for approval',
            owner: null,
            total: 0,
            done: 0,
          ),
        ]);
      }
      if (q['status'] == 'completed') {
        return jsonResponse([
          actionSummaryJson('a3', status: 'completed', label: 'Done', done: 3),
        ]);
      }
      if (q['mine'] == 'true') return jsonResponse([actionSummaryJson('a1')]);
      return jsonResponse([
        actionSummaryJson('a1'),
        actionSummaryJson(
          'a4',
          status: 'overdue',
          label: 'Overdue',
          daysLate: 5,
          target: '2026-10-04',
          done: 0,
        ),
        actionSummaryJson(
          'a5',
          status: 'accepted',
          label: 'Accepted',
          dueSoon: true,
          total: 0,
          done: 0,
        ),
      ]);
    })
    ..json('GET /organizations/b1/actions/summary', {
      'pending': 1,
      'accepted': 1,
      'in_progress': 1,
      'partially_completed': 0,
      'completed': 1,
      'cancelled': 0, 'overdue': 1, 'open': 3, 'due_soon': 1, 'mine_open': 1, //
    })
    ..json('GET /organizations/b1/actions/a1', actionDetailJson('a1'))
    ..json(
      'PATCH /organizations/b1/actions/a1',
      actionDetailJson(
        'a1',
        steps: [
          {'text': 'Ring the supplier', 'done': true},
          {'text': 'Change the order', 'done': true},
          {'text': 'Tell the team', 'done': false},
        ],
      ),
    )
    ..json(
      'POST /organizations/b1/actions/a1/status',
      actionDetailJson(
        'a1',
        status: 'completed',
        label: 'Done',
        next: [],
        followUp: {
          'due_date': '2026-11-14',
          'measure_month': '2026-10-01',
          'status': 'scheduled',
          'is_due': false,
          'notified': false,
        },
      ),
    )
    ..json(
      'POST /organizations/b1/actions/a1/notes',
      actionDetailJson(
        'a1',
        updates: [
          {
            'id': 'up1',
            'kind': 'note',
            'from_status': null,
            'to_status': null,
            'note': 'Spoke to the baker',
            'details': {},
            'user': {'id': 'u1', 'name': 'Jo Baker'},
            'created_at': '2026-10-09T16:00:00',
          },
        ],
      ),
    )
    ..json(
      'GET /organizations/b1/actions/a2',
      actionDetailJson(
        'a2',
        status: 'pending',
        label: 'Waiting for approval',
        next: ['accepted', 'cancelled'],
        steps: [],
      ),
    )
    ..json(
      'POST /organizations/b1/actions/a2/approve',
      actionDetailJson(
        'a2',
        status: 'accepted',
        label: 'Accepted',
        next: ['in_progress', 'partially_completed', 'completed', 'cancelled'],
        steps: [],
      ),
    )
    ..json(
      'POST /organizations/b1/actions/a2/reject',
      actionDetailJson(
        'a2',
        status: 'cancelled',
        label: 'Cancelled',
        next: [],
        steps: [],
      ),
    )
    ..json(
      'GET /organizations/b1/actions/a3',
      actionDetailJson(
        'a3',
        status: 'completed',
        label: 'Done',
        next: [],
        outcome: {
          'outcome': 'successful',
          'label': 'It worked',
          'reason': 'Sales rose by more than hoped.',
          'kpi_name': 'Sales',
          'unit': 'gbp',
          'baseline_period': '2026-09-01',
          'baseline_value': '8717.1',
          'measured_period': '2026-10-01',
          'measured_value': '9400',
          'expected_change': '450',
          'actual_change': '682.9',
          'seasonal_change': null,
          'adjusted_change': null,
          'achieved_pct': 152,
          'data_quality': 95,
          'measured_at': '2026-11-15T07:00:00',
          'measured_by': null,
          'alternative': null,
        },
      ),
    )
    ..json(
      'GET /organizations/b1/actions/a5',
      actionDetailJson(
        'a5',
        status: 'accepted',
        label: 'Accepted',
        next: ['in_progress', 'partially_completed', 'completed', 'cancelled'],
        steps: [],
        followUp: null,
      ),
    )
    ..json('GET /organizations/b2/actions', [
      actionSummaryJson('a9', status: 'accepted', label: 'Accepted'),
    ])
    ..json('GET /organizations/b2/actions/summary', {
      'pending': 0,
      'accepted': 1,
      'in_progress': 0,
      'partially_completed': 0,
      'completed': 0,
      'cancelled': 0,
      'overdue': 0,
      'open': 1,
      'due_soon': 0,
      'mine_open': 0,
    })
    ..json(
      'GET /organizations/b2/actions/a9',
      actionDetailJson(
        'a9',
        status: 'accepted',
        label: 'Accepted',
        next: ['in_progress'],
      ),
    )
    ..json('GET /organizations/b1/recommendations', [
      {
        'id': 'rec1',
        'event_id': 'ev1',
        'kpi_name': 'Sales',
        'period_start': '2026-09-01',
        'status': 'open',
        'headline': 'Sales fell 11.9% in September',
        'recommended': 'Re-price o1',
        'score': 82,
        'generated_at': '2026-10-09T10:00:00',
      },
    ])
    ..json(
      'GET /organizations/b1/changes/ev1/recommendation',
      recommendationJson(),
    )
    ..json(
      'POST /organizations/b1/changes/ev1/recommendation/accept',
      actionDetailJson(
        'a1',
        status: 'accepted',
        label: 'Accepted',
        next: ['in_progress', 'completed', 'cancelled'],
      ),
    )
    ..on(
      'POST /organizations/b1/changes/ev1/recommendation/dismiss',
      (_) => http.Response('', 204),
    )
    ..json('GET /organizations/b2/recommendations', [])
    ..json(
      'GET /organizations/b2/changes/ev1/recommendation',
      recommendationJson(),
    )
    ..json('GET /organizations/b1/kpis', kpisJson())
    ..json('GET /organizations/b1/kpis/sales', kpiHistoryJson())
    ..json('GET /organizations/b1/business-health', healthJson())
    ..json('GET /organizations/b1/business-health/history', healthHistoryJson())
    ..json('GET /organizations/b1/forecasts', {
      'kpis': ['sales', 'customers'],
      'figures': [
        {'code': 'sales', 'name': 'Sales', 'unit': 'gbp', 'group': 'Sales'},
        {
          'code': 'customers',
          'name': 'Customers',
          'unit': 'count',
          'group': 'Customer demand',
        },
      ],
    })
    ..json('GET /organizations/b1/forecasts/sales', forecastJson())
    ..json(
      'GET /organizations/b1/forecasts/customers',
      forecastJson(status: 'insufficient_data', name: 'Customers'),
    )
    ..json('GET /organizations/b1/forecasts/sales/accuracy', {
      'kpi_code': 'sales',
      'kpi_name': 'Sales',
      'unit': 'gbp',
      'enough_data': true,
      'checked': 5,
      'within_range': 4,
      'within_range_pct': '80',
      'promised_pct': 80,
      'typical_miss': '700',
      'typical_miss_pct': '8.0',
      'bias_pct': '1.0',
      'headline':
          'Forecasts for sales have landed in their range 4 times out of 5.',
      'verdict': 'Good enough to plan with.',
      'by_months_ahead': [],
      'rows': [],
    })
    ..json('GET /organizations/b1/forecasts/stock-requirements', stockJson())
    ..on('GET /organizations/b1/changes', (request) {
      final effect = request.url.queryParameters['effect'];
      final all = [
        changeJson('ch1'),
        changeJson('ch2', effect: 'good', severity: 'notable', season: true),
      ];
      return jsonResponse([
        for (final c in all)
          if (effect == null || c['effect'] == effect) c,
      ]);
    })
    ..json('GET /organizations/b1/changes/ch1/diagnosis', diagnosisJson())
    ..on(
      'GET /organizations/b1/changes/ch2/diagnosis',
      (_) => errorResponse(404, 'diagnosis_not_found', 'Not explained yet'),
    )
    ..json(
      'GET /organizations/b1/changes/ch1/recommendation',
      recommendationJson(),
    )
    ..json('GET /organizations/b2/kpis', {
      'granularity': 'month',
      'last_run': null,
      'kpis': [],
    })
    ..on(
      'GET /organizations/b2/business-health',
      (_) => http.Response(
        'null',
        200,
        headers: {'content-type': 'application/json'},
      ),
    )
    ..json('GET /organizations/b2/business-health/history', {'points': []})
    ..json('GET /organizations/b2/forecasts', {
      'kpis': ['sales'],
      'figures': [
        {'code': 'sales', 'name': 'Sales', 'unit': 'gbp', 'group': 'Sales'},
      ],
    })
    ..on(
      'GET /organizations/b2/forecasts/sales',
      (_) => http.Response(
        'null',
        200,
        headers: {'content-type': 'application/json'},
      ),
    )
    ..on(
      'GET /organizations/b2/forecasts/stock-requirements',
      (_) => errorResponse(404, 'no_stock', 'No stock records'),
    )
    ..json('GET /organizations/b2/changes', [
      changeJson('ch2', effect: 'good', severity: 'notable'),
    ])
    ..on(
      'GET /organizations/b2/changes/ch2/diagnosis',
      (_) => errorResponse(404, 'diagnosis_not_found', 'Not explained yet'),
    );
  return server;
}

Map<String, dynamic> jsonBodyOf(http.Request request) =>
    jsonDecode(request.body) as Map<String, dynamic>;

class Harness {
  Harness(this.server, {SessionStore? store})
    : store = store ?? MemorySessionStore() {
    api = ApiClient(baseUrl: baseUrl, client: server.client);
    auth = AuthController(
      api: api,
      store: this.store,
      deviceName: 'Test phone',
      appVersion: '1.0.0',
    );
  }

  final FakeServer server;
  final SessionStore store;
  late final ApiClient api;
  late final AuthController auth;

  MemorySessionStore get memory => store as MemorySessionStore;
}

Future<Harness> pumpApp(
  WidgetTester tester,
  FakeServer server, {
  SessionStore? store,
  bool start = true,
}) async {
  final harness = Harness(server, store: store);
  await tester.pumpWidget(
    VyterlixApp(auth: harness.auth, api: harness.api, store: harness.store),
  );
  if (start) {
    await harness.auth.start();
  }
  await tester.pumpAndSettle();
  return harness;
}

Future<void> logIn(
  WidgetTester tester, {
  String email = 'jo@example.co.uk',
  String password = 'a long password',
}) async {
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Email address'),
    email,
  );
  await tester.enterText(
    find.widgetWithText(TextFormField, 'Password'),
    password,
  );
  await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
  await tester.pumpAndSettle();
}
