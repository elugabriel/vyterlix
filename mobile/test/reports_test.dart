import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models_reports.dart';

import 'support.dart';

Map<String, dynamic> summary(
  String id, {
  String trigger = 'manual',
  bool emailed = false,
}) => {
  'id': id,
  'report_id': 'r1',
  'kind': 'monthly',
  'title': 'Monthly report: September 2026',
  'trigger': trigger,
  'period_start': '2026-09-01',
  'period_end': '2026-09-30',
  'generated_at': '2026-10-09T13:05:00',
  'emailed': emailed,
};

Map<String, dynamic> runJson(String id) => {
  ...summary(id),
  'rules_version': '1',
  'content': {
    'title': 'Monthly report: September 2026',
    'facts': ['Written 09/10/2026 for Fakeham Bakery.'],
    'sections': [
      {
        'heading': 'How you did',
        'paragraphs': ['Sales were £9,000.00.'],
        'bullets': ['Profit rose', 'Costs fell'],
        'table': {
          'columns': ['Month', 'Sales'],
          'rows': [
            ['Sep 2026', '£9,000.00'],
            ['Aug 2026', null],
          ],
        },
      },
      {
        'heading': 'Next steps',
        'paragraphs': ['Order flour.'],
        'bullets': [],
        'table': null,
      },
    ],
    'notes': ['Figures are before VAT.'],
  },
};

FakeServer reportsServer({bool withRun = true}) => happyServer()
  ..json('GET /organizations/b1/reports', [
    {
      'id': 'r1',
      'kind': 'monthly',
      'name': 'Monthly report',
      'description': 'How the month went.',
      'months': 1,
      'fixed_months': true,
      'last_run': withRun ? summary('run1') : null,
      'schedules': [],
    },
    {
      'id': 'r2',
      'kind': 'quarterly',
      'name': 'Trend report',
      'description': 'The longer view.',
      'months': 6,
      'fixed_months': false,
      'last_run': null,
      'schedules': [],
    },
  ])
  ..json(
    'GET /organizations/b1/reports/runs',
    withRun
        ? [
            summary('run1'),
            summary('run2', trigger: 'scheduled', emailed: true),
          ]
        : [],
  )
  ..json('GET /organizations/b1/reports/runs/run1', runJson('run1'))
  ..json('GET /organizations/b1/reports/runs/run3', runJson('run3'))
  ..json('POST /organizations/b1/reports/r1/generate', runJson('run3'), 201);

Future<void> open(
  WidgetTester tester,
  FakeServer server, {
  String shop = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 3200);
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
  await tester.tap(find.text('Reports'));
  await tester.pumpAndSettle();
}

void main() {
  test('a written report is read, with empty table cells as a dash', () {
    final r = ReportRun.fromJson(runJson('x'));
    expect(r.title, 'Monthly report: September 2026');
    expect(r.sections.first.rows.last, ['Aug 2026', '–']);
    expect(r.sections.last.columns, isEmpty);
    expect(r.notes, ['Figures are before VAT.']);
    expect(
      ReportRunSummary.fromJson(
        summary('a', trigger: 'scheduled', emailed: true),
      ).how,
      'Sent to you',
    );
    expect(
      ReportRunSummary.fromJson(summary('a', trigger: 'scheduled')).how,
      'Scheduled',
    );
    expect(ReportRunSummary.fromJson(summary('a')).how, 'By you');
  });

  testWidgets('lists the reports and what has been written', (tester) async {
    await open(tester, reportsServer());
    expect(find.text('Monthly report'), findsOneWidget);
    expect(find.text('Covers the latest month'), findsOneWidget);
    expect(find.text('Covers the last 6 months'), findsOneWidget);
    expect(find.text('Nothing written for you yet'), findsOneWidget);
    expect(
      find.textContaining('Open the latest (09/10/2026, 13:05)'),
      findsOneWidget,
    );
    expect(find.text('Write it now'), findsNWidgets(2));
    expect(
      find.text('2026-09 to 2026-09 · 09/10/2026, 13:05 · By you'),
      findsOneWidget,
    );
    expect(
      find.text('2026-09 to 2026-09 · 09/10/2026, 13:05 · Sent to you'),
      findsOneWidget,
    );
  });

  testWidgets('with nothing written the history says so', (tester) async {
    await open(tester, reportsServer(withRun: false));
    expect(find.text('None yet. Write one above.'), findsOneWidget);
  });

  testWidgets('a viewer can read reports but not write them', (tester) async {
    final server = reportsServer();
    for (final k in server.routes.keys.toList()) {
      if (k.contains('/organizations/b1/')) {
        server.routes[k.replaceFirst('/b1/', '/b2/')] = server.routes[k]!;
      }
    }
    await open(tester, server, shop: 'Second Shop');
    expect(find.text('Write it now'), findsNothing);
    expect(find.text('Monthly report'), findsOneWidget);
  });

  testWidgets('opening one shows it as it was written', (tester) async {
    await open(tester, reportsServer());
    await tester.tap(find.textContaining('Open the latest'));
    await tester.pumpAndSettle();
    expect(find.text('Monthly report: September 2026'), findsOneWidget);
    expect(find.text('Written 09/10/2026 for Fakeham Bakery.'), findsOneWidget);
    expect(find.text('How you did'), findsOneWidget);
    expect(find.text('Sales were £9,000.00.'), findsOneWidget);
    expect(find.text('Profit rose'), findsOneWidget);
    expect(find.text('Sep 2026'), findsOneWidget);
    expect(find.text('–'), findsOneWidget);
    expect(find.text('Order flour.'), findsOneWidget);
    expect(find.text('Figures are before VAT.'), findsOneWidget);
  });

  testWidgets('writing one posts, then opens the new report', (tester) async {
    final server = reportsServer();
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('write-r1')));
    await tester.pumpAndSettle();
    expect(
      server.to('POST /organizations/b1/reports/r1/generate'),
      hasLength(1),
    );
    expect(server.to('GET /organizations/b1/reports/runs/run3'), hasLength(1));
    expect(find.text('How you did'), findsOneWidget);
  });

  testWidgets('a refusal when writing is shown', (tester) async {
    final server = reportsServer()
      ..on(
        'POST /organizations/b1/reports/r1/generate',
        (_) => errorResponse(
          409,
          'no_results',
          'There are no results to write up yet.',
        ),
      );
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('write-r1')));
    await tester.pumpAndSettle();
    expect(find.text('There are no results to write up yet.'), findsOneWidget);
  });

  testWidgets('a problem loading can be tried again', (tester) async {
    final server = reportsServer()
      ..on(
        'GET /organizations/b1/reports',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
    await open(tester, server);
    expect(find.text('An unexpected error occurred'), findsOneWidget);
    server.routes.remove('GET /organizations/b1/reports');
    final fresh = reportsServer();
    server.routes['GET /organizations/b1/reports'] =
        fresh.routes['GET /organizations/b1/reports']!;
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Monthly report'), findsOneWidget);
  });
}
