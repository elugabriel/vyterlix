import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models_memory.dart';

import 'support.dart';

Map<String, dynamic> memoryJson({bool empty = false}) => {
  'normal_ranges': empty
      ? []
      : [
          {'statement': 'Sales are normally £7,000 to £9,000 a month.'},
        ],
  'customer_patterns': empty
      ? []
      : [
          {'statement': '60% of customers come back.'},
        ],
  'goals': <dynamic>[],
  'seasons': empty
      ? []
      : [
          {'statement': 'December is your busiest month.'},
        ],
  'constraints': {
    'max_cost_level': 'low',
    'max_effort': null,
    'excluded_actions': ['discount'],
    'excluded_names': ['Run a discount'],
    'quick_results_only': false,
    'updated_at': null,
  },
  'lessons': empty
      ? []
      : [
          {
            'action': 'Email offer',
            'kpi_code': 'x',
            'kpi_name': 'Sales',
            'outcome': 'successful',
            'achieved_pct': 120,
            'lesson': 'Email offers lifted sales.',
          },
        ],
  'patterns': empty
      ? []
      : [
          {
            'action': 'Email offer',
            'code': 'e',
            'kpi_code': 'x',
            'kpi_name': 'Sales',
            'successful': 2,
            'partially_successful': 1,
            'unsuccessful': 0,
            'inconclusive': 0,
            'average_achieved_pct': '110',
            'last_outcome': 'successful',
          },
        ],
  'recent_use': empty
      ? []
      : [
          {
            'id': 'u1',
            'purpose': 'x',
            'event_id': null,
            'used': [
              {'statement': 'Never suggest discounts.'},
            ],
            'created_at': '2026-10-09T13:05:00',
          },
        ],
  'last_rebuilt': empty ? null : '2026-10-08T09:00:00',
};

FakeServer memoryServer({bool empty = false}) => happyServer()
  ..json('GET /organizations/b1/memory', memoryJson(empty: empty))
  ..json('GET /organizations/b1/interventions', [
    {'code': 'discount', 'name': 'Run a discount'},
    {'code': 'email', 'name': 'Send an email offer'},
  ])
  ..json('POST /organizations/b1/memory/rebuild', {
    'normal_ranges': 1,
    'customer_patterns': 0,
    'goals': 0,
    'seasons': 0,
    'removed': 0,
  })
  ..json(
    'PUT /organizations/b1/memory/constraints',
    memoryJson()['constraints']!,
  );

Future<void> open(
  WidgetTester tester,
  FakeServer server, {
  String shop = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 4000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
  );
  await tester.tap(find.text(shop));
  await tester.pumpAndSettle();
  await tester.tap(
    find.descendant(
      of: find.byType(NavigationBar),
      matching: find.text('More'),
    ),
  );
  await tester.pumpAndSettle();
  await tester.tap(find.text('What we know'));
  await tester.pumpAndSettle();
}

void main() {
  test('what we know is read', () {
    final m = Memory.fromJson(memoryJson());
    expect(
      m.normalRanges.single,
      'Sales are normally £7,000 to £9,000 a month.',
    );
    expect(m.limits.excluded, {'discount'});
    expect(m.lessons.single.outcomeText, 'It worked');
    expect(
      m.patterns.single.summary,
      'worked 2 · partly 1 · did not 0 · could not tell 0 · usually 110% of what was expected',
    );
  });

  testWidgets('shows everything we know, in plain English', (tester) async {
    await open(tester, memoryServer());
    expect(
      find.text('Sales are normally £7,000 to £9,000 a month.'),
      findsOneWidget,
    );
    expect(find.text('60% of customers come back.'), findsOneWidget);
    expect(find.text('You have not set any goals.'), findsOneWidget);
    expect(find.text('December is your busiest month.'), findsOneWidget);
    expect(find.text('It worked'), findsOneWidget);
    expect(find.text('Email offers lifted sales.'), findsOneWidget);
    expect(find.text('Email offer: Sales'), findsOneWidget);
    expect(find.text('Never suggest discounts.'), findsOneWidget);
    expect(
      find.textContaining('last worked out on 08/10/2026'),
      findsOneWidget,
    );
  });

  testWidgets('says what is missing when there is nothing yet', (tester) async {
    await open(tester, memoryServer(empty: true));
    expect(find.textContaining('Half a year is needed'), findsOneWidget);
    expect(
      find.textContaining('Bring in your sales and customers first.'),
      findsOneWidget,
    );
    expect(find.text('No seasons set.'), findsOneWidget);
    expect(
      find.textContaining('When an action you took has been checked'),
      findsOneWidget,
    );
    expect(
      find.textContaining('whenever a suggestion is made'),
      findsOneWidget,
    );
  });

  testWidgets('the owner can change and save the limits', (tester) async {
    final server = memoryServer();
    await open(tester, server);
    expect(
      tester
          .widget<CheckboxListTile>(
            find.widgetWithText(CheckboxListTile, 'Run a discount'),
          )
          .value,
      isTrue,
    );
    await tester.tap(find.byKey(const ValueKey('limit-effort')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('A little effort').last);
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Switch));
    await tester.tap(find.text('Send an email offer'));
    await tester.tap(find.text('Run a discount'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Save my limits'));
    await tester.tap(find.text('Save my limits'));
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(
        server.to('PUT /organizations/b1/memory/constraints').single,
      ),
      {
        'max_cost_level': 'low',
        'max_effort': 'low',
        'excluded_actions': ['email'],
        'quick_results_only': true,
      },
    );
    expect(find.text('Your limits are saved.'), findsOneWidget);
  });

  testWidgets('a viewer can only read', (tester) async {
    final server = memoryServer();
    for (final k in server.routes.keys.toList()) {
      if (k.contains('/b1/')) {
        server.routes[k.replaceFirst('/b1/', '/b2/')] = server.routes[k]!;
      }
    }
    await open(tester, server, shop: 'Second Shop');
    expect(find.text('Refresh what we know'), findsNothing);
    expect(find.text('Save my limits'), findsNothing);
    expect(
      find.text('Only the owner can change these limits.'),
      findsOneWidget,
    );
  });

  testWidgets('refreshing asks the server and says so', (tester) async {
    final server = memoryServer();
    await open(tester, server);
    await tester.tap(find.text('Refresh what we know'));
    await tester.pumpAndSettle();
    expect(server.to('POST /organizations/b1/memory/rebuild'), hasLength(1));
    expect(find.text('Refreshed.'), findsOneWidget);
  });

  testWidgets('a refusal when saving is shown', (tester) async {
    final server = memoryServer()
      ..on(
        'PUT /organizations/b1/memory/constraints',
        (_) => errorResponse(403, 'forbidden', 'Only the owner can do that.'),
      );
    await open(tester, server);
    await tester.ensureVisible(find.text('Save my limits'));
    await tester.tap(find.text('Save my limits'));
    await tester.pumpAndSettle();
    expect(find.text('Only the owner can do that.'), findsOneWidget);
  });

  testWidgets('a problem loading can be tried again', (tester) async {
    final server = memoryServer()
      ..on(
        'GET /organizations/b1/memory',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
    await open(tester, server);
    expect(find.text('An unexpected error occurred'), findsOneWidget);
    server.json('GET /organizations/b1/memory', memoryJson());
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('60% of customers come back.'), findsOneWidget);
  });
}
