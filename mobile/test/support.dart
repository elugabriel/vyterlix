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
    );
  return server;
}

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
