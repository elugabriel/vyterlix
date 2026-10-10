import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/format.dart';
import 'package:vyterlix_mobile/models_insights.dart';
import 'package:vyterlix_mobile/theme.dart';
import 'package:vyterlix_mobile/widgets/charts.dart';

import 'support.dart';

Future<Harness> openBusiness(
  WidgetTester tester,
  FakeServer server, {
  String name = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 3000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final h = await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
  );
  await tester.tap(find.text(name));
  await tester.pumpAndSettle();
  return h;
}

Future<void> openMore(WidgetTester tester, String entry) async {
  await tester.tap(
    find.descendant(
      of: find.byType(NavigationBar),
      matching: find.text('More'),
    ),
  );
  await tester.pumpAndSettle();
  await tester.tap(find.text(entry));
  await tester.pumpAndSettle();
}

Color? colourOf(WidgetTester tester, String text) =>
    tester.widget<Text>(find.text(text)).style?.color;

void main() {
  group('what comes from the server', () {
    test('figures are read with their latest and current months', () {
      final kpis = [
        for (final k in kpisJson()['kpis'] as List)
          Kpi.fromJson(k as Map<String, dynamic>),
      ];
      final sales = kpis.first;
      expect(
        (sales.code, sales.category, sales.unit, sales.direction),
        ('sales', 'sales', 'gbp', 'up_good'),
      );
      expect(sales.latest!.value, '8717.10');
      expect(sales.latest!.changePct, 9.1);
      expect(sales.current!.isComplete, isFalse);
      expect(kpis[3].latest!.status, 'no_data');
      expect(kpis.last.latest, isNull);
    });

    test('a figure over time is read oldest first', () {
      final h = KpiHistory.fromJson(kpiHistoryJson());
      expect(h.values.map((v) => v.periodStart), [
        '2026-07-01',
        '2026-08-01',
        '2026-09-01',
        '2026-10-01',
      ]);
      expect(h.values.last.isComplete, isFalse);
      expect(h.formula, startsWith('Sales:'));
    });

    test('health is read with its areas and their weakest parts', () {
      final h = HealthReport.fromJson(healthJson());
      expect(
        (h.overallScore, h.previousScore, h.trend, h.coveragePct),
        (69, 89, 'down', 80),
      );
      expect(h.components.map((c) => c.label), ['Money', 'Sales']);
      expect(h.components.last.metrics.single.score, 30.0);
      expect(
        HealthPoint.fromJson(
          (healthHistoryJson()['points'] as List).first as Map<String, dynamic>,
        ).score,
        85,
      );
    });

    test('a forecast is read with its range and what happened', () {
      final f = Forecast.fromJson(forecastJson());
      expect(
        (f.name, f.status, f.intervalLevel, f.methodName),
        ('Sales', 'ok', 80, 'Recent average'),
      );
      expect(f.predictions.map((p) => p.value), [9117.97, 9200.0, 12765.16]);
      expect(f.predictions[1].actual, 9400.0);
      expect(f.predictions[0].actual, isNull);
      expect(f.history.first, ('2026-06-01', 7000.0));
      final s = StockRequirements.fromJson(stockJson());
      expect(
        (s.orderNow, s.rows.first.status, s.rows.first.daysOfCover),
        (1, 'order_now', 4),
      );
    });

    test('a change and its explanation are read', () {
      final c = Change.fromJson(changeJson('x', season: true));
      expect(
        (c.effect, c.severity, c.explainedBySeason, c.status),
        ('bad', 'major', true, 'open'),
      );
      final d = Diagnosis.fromJson(diagnosisJson());
      expect(
        (d.confidence, d.confidenceLabel, d.evidence.length),
        (72, 'medium', 2),
      );
      expect(d.evidence.first.type, 'fact');
    });
  });

  group('words and numbers', () {
    test('a change is written as the website writes it', () {
      expect(
        changeSentence(
          status: 'ok',
          value: '110',
          previousValue: '100',
          changePct: 10.0,
          unit: 'gbp',
        ),
        (sign: 1, text: 'up 10.0% on the month before'),
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '90',
          previousValue: '100',
          changePct: -10.0,
          unit: 'gbp',
        ),
        (sign: -1, text: 'down 10.0% on the month before'),
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '100',
          previousValue: '100',
          changePct: 0.0,
          unit: 'gbp',
        ),
        (sign: 0, text: 'no change on the month before'),
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '75.6',
          previousValue: '70.0',
          changePct: 8.0,
          unit: 'percent',
        ),
        (sign: 1, text: 'up 5.6 points on the month before'),
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '50.0',
          previousValue: '60.0',
          changePct: null,
          unit: 'percent',
        ),
        (sign: -1, text: 'down 10.0 points on the month before'),
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '5',
          previousValue: '0',
          changePct: null,
          unit: 'gbp',
        ),
        (sign: 0, text: 'was £0.00 the month before'),
      );
      expect(
        changeSentence(
          status: 'no_data',
          value: null,
          previousValue: null,
          changePct: null,
          unit: 'gbp',
        ),
        isNull,
      );
      expect(
        changeSentence(
          status: 'ok',
          value: '5',
          previousValue: null,
          changePct: null,
          unit: 'gbp',
        ),
        isNull,
      );
    });

    test('a missing figure says what it needs', () {
      expect(
        missingText('no_data', ['sales', 'expenses']),
        "Needs sales and expenses, which you haven't added yet.",
      );
      expect(
        missingText('no_data', ['customer_sales']),
        "Needs sales that say which customer bought, which you haven't added yet.",
      );
      expect(
        missingText('no_data', []),
        "Needs records you haven't added yet.",
      );
      expect(
        missingText('undefined', ['sales']),
        startsWith("Can't be worked out for this period"),
      );
    });

    test('a value sits between the bottom and the top of a chart', () {
      expect(fractionBetween(5, 0, 10), 0.5);
      expect(fractionBetween(0, 0, 10), 0.0);
      expect(fractionBetween(10, 0, 10), 1.0);
      expect(fractionBetween(-5, 0, 10), 0.0);
      expect(fractionBetween(15, 0, 10), 1.0);
      expect(fractionBetween(3, 3, 3), 0.5);
      expect(goodFairPoor(80), Palette.ok);
      expect(goodFairPoor(50), Palette.warn);
      expect(goodFairPoor(10), Palette.bad);
    });
  });

  group('the More tab', () {
    testWidgets('lists where to find each of the figures', (tester) async {
      await openBusiness(tester, happyServer());
      await tester.tap(
        find.descendant(
          of: find.byType(NavigationBar),
          matching: find.text('More'),
        ),
      );
      await tester.pumpAndSettle();
      for (final title in [
        'Key figures',
        'Business health',
        'Forecast',
        'What changed',
      ]) {
        expect(find.text(title), findsOneWidget);
      }
      expect(
        find.text('Every figure about your business, month by month.'),
        findsOneWidget,
      );
    });
  });

  group('Key figures', () {
    testWidgets(
      'are grouped like the website, with the latest month and how it changed',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'Key figures');
        expect(
          find.text(
            'Worked out on 09/10/2026, 10:47. They update by themselves after an import.',
          ),
          findsOneWidget,
        );
        for (final group in ['Money', 'Sales', 'Customers']) {
          expect(find.text(group), findsWidgets);
        }
        expect(find.text('Stock'), findsNothing); // nothing in that group
        expect(
          tester.getTopLeft(find.text('Money')).dy,
          lessThan(tester.getTopLeft(find.text('Sales').first).dy),
        );
        expect(find.text('£8,717.10'), findsOneWidget);
        expect(find.text('75.6%'), findsOneWidget);
        expect(find.text('September 2026'), findsWidgets);
        expect(find.text('▲ up 9.1% on the month before'), findsOneWidget);
        expect(
          find.text('▲ up 5.6 points on the month before'),
          findsOneWidget,
        );
      },
    );

    testWidgets(
      'colour good news green and bad news red by which way is good',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'Key figures');
        expect(
          colourOf(tester, '▲ up 9.1% on the month before'),
          Palette.ok,
        ); // sales up is good
        expect(
          colourOf(tester, '▲ up 8.8% on the month before'),
          Palette.bad,
        ); // costs up is bad
      },
    );

    testWidgets('a figure that cannot be worked out says what it needs', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Key figures');
      expect(
        find.text("Needs sales and expenses, which you haven't added yet."),
        findsOneWidget,
      );
      expect(find.text('Not worked out yet.'), findsOneWidget);
    });

    testWidgets('say so when nothing has been worked out', (tester) async {
      await openBusiness(tester, happyServer(), name: 'Second Shop');
      await openMore(tester, 'Key figures');
      expect(
        find.textContaining('have not been worked out yet'),
        findsOneWidget,
      );
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/kpis',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await openMore(tester, 'Key figures');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/kpis', kpisJson());
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('£8,717.10'), findsOneWidget);
    });

    testWidgets(
      'one figure shows a chart, the months newest first and how it is worked out',
      (tester) async {
        final server = happyServer();
        await openBusiness(tester, server);
        await openMore(tester, 'Key figures');
        await tester.tap(find.text('£8,717.10'));
        await tester.pumpAndSettle();
        expect(
          server
              .to('GET /organizations/b1/kpis/sales')
              .single
              .url
              .queryParameters,
          {'limit': '24'},
        );
        expect(find.text('About Sales'), findsOneWidget);
        expect(find.byKey(const ValueKey('kpi-chart')), findsOneWidget);
        expect(find.text('October 2026 (so far)'), findsOneWidget);
        expect(find.text('£1,200.00'), findsOneWidget);
        expect(find.text('▼ down 86.2% on the month before'), findsOneWidget);
        expect(
          tester.getTopLeft(find.text('October 2026 (so far)')).dy,
          lessThan(tester.getTopLeft(find.text('July 2026')).dy),
        );
        await tester.tap(find.text('How it is worked out'));
        await tester.pumpAndSettle();
        expect(
          find.text('Sales: the money taken, net of VAT, after refunds.'),
          findsOneWidget,
        );
      },
    );

    testWidgets('one figure with a problem loading can be tried again', (
      tester,
    ) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/kpis/sales',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await openMore(tester, 'Key figures');
      await tester.tap(find.text('£8,717.10'));
      await tester.pumpAndSettle();
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/kpis/sales', kpiHistoryJson());
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('kpi-chart')), findsOneWidget);
    });
  });

  group('Business health', () {
    testWidgets('opens with the score, how it moved and why', (tester) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Business health');
      expect(find.byKey(const ValueKey('health-overall')), findsOneWidget);
      expect(find.text('69'), findsOneWidget);
      expect(find.text('Fair'), findsOneWidget);
      expect(
        find.text('▼ down from 89 the month before · September 2026'),
        findsOneWidget,
      );
      expect(
        find.text(
          'Your business is doing fairly well; sales are holding it back.',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'This score covers 80% of the picture: the rest could not be measured yet.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('shows the months so far and the areas behind the score', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Business health');
      expect(find.byKey(const ValueKey('health-history')), findsOneWidget);
      expect(find.text('June 2026 to September 2026'), findsOneWidget);
      expect(find.text('Money'), findsOneWidget);
      expect(find.text('82'), findsOneWidget);
      expect(find.text('Healthy'), findsOneWidget);
      expect(find.text('At risk'), findsOneWidget);
    });

    testWidgets('an area opens to show what drives it, weakest first', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Business health');
      expect(find.text('Sales growth'), findsNothing);
      await tester.tap(find.text('Sales').last);
      await tester.pumpAndSettle();
      expect(find.text('Sales fell a lot.'), findsOneWidget);
      expect(find.text('Sales growth'), findsOneWidget);
      expect(
        find.text(
          'Sales growth was -11.9% against a usual -0.7%, which scored 30.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('says so when it has not been worked out', (tester) async {
      await openBusiness(tester, happyServer(), name: 'Second Shop');
      await openMore(tester, 'Business health');
      expect(
        find.textContaining('has not been worked out yet'),
        findsOneWidget,
      );
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/business-health',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await openMore(tester, 'Business health');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/business-health', healthJson());
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('69'), findsOneWidget);
    });
  });

  group('Forecast', () {
    testWidgets('shows what we expect, the range around it and what happened', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Forecast');
      expect(find.text('Sales: the next 3 months'), findsOneWidget);
      expect(
        find.text('We expect Sales of about £9,117.97 in October.'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('forecast-chart')), findsOneWidget);
      expect(
        find.textContaining('the range it should land in, 80 times out of 100'),
        findsOneWidget,
      );
      expect(find.text('We expect £9,117.97'), findsOneWidget);
      expect(
        find.text('Most likely between £7,836.51 and £10,399.44'),
        findsOneWidget,
      );
      expect(find.text('Not finished yet'), findsNWidgets(2));
      expect(find.text('What happened: £9,400.00'), findsOneWidget);
      expect(find.text('Method: Recent average'), findsOneWidget);
    });

    testWidgets('says how accurate it has been', (tester) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Forecast');
      expect(
        find.text(
          'Forecasts for sales have landed in their range 4 times out of 5.',
        ),
        findsOneWidget,
      );
      expect(find.text('Good enough to plan with.'), findsOneWidget);
    });

    testWidgets('says what to stock, most urgent first', (tester) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Forecast');
      expect(find.text('What to stock'), findsOneWidget);
      expect(
        find.text('1 product to order now, 1 to keep an eye on.'),
        findsOneWidget,
      );
      expect(find.text('Order now'), findsOneWidget);
      expect(find.text('Fine'), findsOneWidget);
      expect(
        find.text(
          'Expect to sell 300. In stock: 40, which lasts about 4 days.',
        ),
        findsOneWidget,
      );
      expect(
        find.text('Order about 320 to cover a busy month.'),
        findsOneWidget,
      );
      expect(
        find.text('Order about 130 to cover a busy month.'),
        findsNothing,
      ); // fine: nothing to order
      expect(
        tester.getTopLeft(find.text('Sourdough loaf')).dy,
        lessThan(tester.getTopLeft(find.text('Rye loaf')).dy),
      );
    });

    testWidgets('another figure can be chosen', (tester) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await openMore(tester, 'Forecast');
      await tester.tap(find.widgetWithText(ChoiceChip, 'Customers'));
      await tester.pumpAndSettle();
      expect(
        server.to('GET /organizations/b1/forecasts/customers'),
        hasLength(1),
      );
      expect(
        find.text('There is not enough history to forecast yet.'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('forecast-chart')), findsNothing);
    });

    testWidgets('one not worked out yet can be worked out by an owner', (
      tester,
    ) async {
      var made = false;
      final server = happyServer()
        ..on(
          'GET /organizations/b1/forecasts/sales',
          (_) => made
              ? jsonResponse(forecastJson())
              : http.Response(
                  'null',
                  200,
                  headers: {'content-type': 'application/json'},
                ),
        )
        ..on('POST /organizations/b1/forecasts/sales', (_) {
          made = true;
          return jsonResponse(forecastJson());
        });
      await openBusiness(tester, server);
      await openMore(tester, 'Forecast');
      expect(
        find.text('This forecast has not been worked out yet.'),
        findsOneWidget,
      );
      await tester.tap(find.text('Work it out now'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/forecasts/sales'), hasLength(1));
      expect(find.byKey(const ValueKey('forecast-chart')), findsOneWidget);
    });

    testWidgets(
      'a viewer is only told it has not been worked out, and no stock section appears without stock',
      (tester) async {
        await openBusiness(tester, happyServer(), name: 'Second Shop');
        await openMore(tester, 'Forecast');
        expect(
          find.text('This forecast has not been worked out yet.'),
          findsOneWidget,
        );
        expect(find.text('Work it out now'), findsNothing);
        expect(find.text('What to stock'), findsNothing);
      },
    );

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/forecasts',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await openMore(tester, 'Forecast');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
    });
  });

  group('What changed', () {
    testWidgets(
      'lists what moved, whether it is good or bad news and how big',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'What changed');
        expect(find.text('Change ch1 summary'), findsOneWidget);
        expect(find.text('Bad news'), findsWidgets);
        expect(find.text('Good news'), findsWidgets);
        expect(find.text('Big change'), findsOneWidget);
        expect(find.text('Sales · September 2026'), findsNWidgets(2));
        expect(
          find.text(
            'About what your busy and quiet seasons lead you to expect.',
          ),
          findsOneWidget,
        );
      },
    );

    testWidgets('can be narrowed to bad news or good news', (tester) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await openMore(tester, 'What changed');
      await tester.tap(find.widgetWithText(ChoiceChip, 'Bad news'));
      await tester.pumpAndSettle();
      expect(find.text('Change ch1 summary'), findsOneWidget);
      expect(find.text('Change ch2 summary'), findsNothing);
      await tester.tap(find.widgetWithText(ChoiceChip, 'Good news'));
      await tester.pumpAndSettle();
      expect(find.text('Change ch2 summary'), findsOneWidget);
      expect(
        server
            .to('GET /organizations/b1/changes')
            .map((r) => r.url.queryParameters['effect']),
        [null, 'bad', 'good'],
      );
    });

    testWidgets('an empty list says so', (tester) async {
      final server = happyServer()..json('GET /organizations/b1/changes', []);
      await openBusiness(tester, server);
      await openMore(tester, 'What changed');
      expect(
        find.text('Nothing has moved by more than normal.'),
        findsOneWidget,
      );
    });

    testWidgets(
      'one change shows why it happened, how sure we are and the evidence',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'What changed');
        await tester.tap(find.text('Change ch1 summary'));
        await tester.pumpAndSettle();
        expect(find.text('Fewer people came in on weekdays'), findsOneWidget);
        expect(
          find.text('Most of the fall came from Tuesdays and Wednesdays.'),
          findsOneWidget,
        );
        expect(
          find.text('How sure we are: medium (72 out of 100).'),
          findsOneWidget,
        );
        expect(find.text('Worked out from sales alone.'), findsOneWidget);
        expect(
          find.text('There were 12 fewer sales on Tuesdays.'),
          findsOneWidget,
        );
        expect(find.text('Fact'), findsOneWidget);
        expect(find.text('Worked out'), findsOneWidget);
      },
    );

    testWidgets('one nobody has looked into can be explained by an owner', (
      tester,
    ) async {
      var made = false;
      final server = happyServer()
        ..on(
          'GET /organizations/b1/changes/ch2/diagnosis',
          (_) => made
              ? jsonResponse(diagnosisJson())
              : errorResponse(404, 'diagnosis_not_found', 'Not explained yet'),
        )
        ..on('POST /organizations/b1/changes/ch2/diagnosis', (_) {
          made = true;
          return jsonResponse(diagnosisJson());
        });
      await openBusiness(tester, server);
      await openMore(tester, 'What changed');
      await tester.tap(find.text('Change ch2 summary'));
      await tester.pumpAndSettle();
      expect(find.text('Nobody has looked into this one yet.'), findsOneWidget);
      await tester.tap(find.text('Work out why'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/changes/ch2/diagnosis'),
        hasLength(1),
      );
      expect(find.text('Fewer people came in on weekdays'), findsOneWidget);
      expect(find.text('Work it out again'), findsOneWidget);
    });

    testWidgets('a viewer is only told it has not been looked into', (
      tester,
    ) async {
      await openBusiness(tester, happyServer(), name: 'Second Shop');
      await openMore(tester, 'What changed');
      await tester.tap(find.text('Change ch2 summary'));
      await tester.pumpAndSettle();
      expect(find.text('Nobody has looked into this one yet.'), findsOneWidget);
      expect(find.text('Work out why'), findsNothing);
    });

    testWidgets('leads on to the ideas for what to do about it', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'What changed');
      await tester.tap(find.text('Change ch1 summary'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('See the ideas'));
      await tester.pumpAndSettle();
      expect(find.text('Idea'), findsOneWidget);
      expect(find.text('Sales fell 11.9% in September'), findsOneWidget);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/changes',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await openMore(tester, 'What changed');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.on(
        'GET /organizations/b1/changes',
        (_) => jsonResponse([changeJson('ch1')]),
      );
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Change ch1 summary'), findsOneWidget);
    });
  });
}
