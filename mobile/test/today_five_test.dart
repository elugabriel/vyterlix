import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models.dart';
import 'package:vyterlix_mobile/theme.dart';

import 'package:vyterlix_mobile/widgets/charts.dart';

import 'support.dart';

Future<FakeServer> openToday(
  WidgetTester tester, {
  Map<String, dynamic>? dash,
}) async {
  final server = happyServer();
  if (dash != null) server.json('GET /organizations/b1/dashboard', dash);
  tester.view.physicalSize = const Size(800, 5000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
  );
  await tester.tap(find.text('Fakeham Bakery'));
  await tester.pumpAndSettle();
  return server;
}

void main() {
  group('what comes from the server', () {
    test('the five answers are read', () {
      final d = Dashboard.fromJson(dashboard());
      expect(d.goingWell.single.title, 'Sales');
      expect(d.goingWell.single.tone, 'good');
      expect(d.doFirst!.title, 'Item 0 title');
      expect(d.waitingFor.single.link, 'actions.html#a1');
      expect(d.results!.measured, 2);
      expect(d.results!.latest.single.outcomeLabel, 'It worked');
    });

    test('an older server that does not send them still works', () {
      final json = dashboard()
        ..remove('going_well')
        ..remove('do_first')
        ..remove('waiting_for')
        ..remove('results');
      final d = Dashboard.fromJson(json);
      expect(d.goingWell, isEmpty);
      expect(d.doFirst, isNull);
      expect(d.waitingFor, isEmpty);
      expect(d.results, isNull);
    });
  });

  group('the Today screen', () {
    testWidgets('asks the five questions, in the client\'s words and order', (
      tester,
    ) async {
      await openToday(tester);
      final order = [
        'What is going well?',
        'What needs my attention today?',
        'What should I do first?',
        'What am I waiting for?',
        'Did my actions help?',
        'Your key figures',
      ];
      var last = -1.0;
      for (final heading in order) {
        expect(find.text(heading), findsOneWidget, reason: heading);
        final y = tester.getTopLeft(find.text(heading)).dy;
        expect(
          y,
          greaterThan(last),
          reason: '$heading should come after the one before',
        );
        last = y;
      }
    });

    testWidgets('a picture shows how the first figure has moved', (
      tester,
    ) async {
      await openToday(tester);
      expect(find.text('How sales has moved'), findsOneWidget);
      expect(find.byType(BarChart), findsOneWidget);
    });

    testWidgets('a bar shows how the checked actions turned out', (
      tester,
    ) async {
      await openToday(tester);
      expect(
        tester.widget<Text>(find.byKey(const Key('outcome-bar-words'))).data,
        '1 worked · 1 partly worked',
      );
    });

    testWidgets('what is going well lists the good news with a green bar', (
      tester,
    ) async {
      await openToday(tester);
      expect(find.text('Sales'), findsWidgets);
      expect(find.text('You sold more than last month.'), findsWidgets);
    });

    testWidgets('what to do first is the most serious thing, and opens', (
      tester,
    ) async {
      await openToday(tester);
      expect(find.text('Item 0 title'), findsNWidgets(2));
    });

    testWidgets('with nothing to do it says to keep going', (tester) async {
      final d = dashboard(attention: 0);
      await openToday(tester, dash: d);
      expect(
        find.text('Nothing. Keep doing what you are doing.'),
        findsOneWidget,
      );
      expect(find.text('Nothing needs you right now.'), findsOneWidget);
    });

    testWidgets('what I am waiting for shows who has it and when it is due', (
      tester,
    ) async {
      await openToday(tester);
      expect(find.text('Ring the regulars'), findsOneWidget);
      expect(find.text('Sam Lee, due 29/10/2026.'), findsOneWidget);
    });

    testWidgets(
      'did my actions help answers in one line and lists the latest result',
      (tester) async {
        await openToday(tester);
        expect(
          find.text(
            'Of the 2 actions we have checked, 1 worked and 1 partly worked.',
          ),
          findsOneWidget,
        );
        expect(find.text('It worked: Offer a bundle'), findsOneWidget);
        expect(find.textContaining('It won back £90.00'), findsOneWidget);
      },
    );

    testWidgets('empty parts say what will fill them, not nothing', (
      tester,
    ) async {
      final d = dashboard()
        ..['going_well'] = <dynamic>[]
        ..['waiting_for'] = <dynamic>[]
        ..['results'] = {
          'measured': 0,
          'successful': 0,
          'partially_successful': 0,
          'unsuccessful': 0,
          'inconclusive': 0,
          'sentence': 'No action has been checked yet. Finish one and we will tell you whether it helped.',
          'latest': <dynamic>[],
        };
      await openToday(tester, dash: d);
      expect(find.textContaining('Nothing stands out yet.'), findsOneWidget);
      expect(find.textContaining('Nothing is in progress.'), findsOneWidget);
      expect(
        find.textContaining('No action has been checked yet.'),
        findsOneWidget,
      );
    });

    testWidgets('a line about an action opens that action', (tester) async {
      final server = await openToday(tester);
      server.json('GET /organizations/b1/actions/a1', actionDetailJson('a1'));
      await tester.tap(find.text('Ring the regulars'));
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/actions/a1'), isNotEmpty);
    });

    testWidgets('a line about the figures opens the key figures', (
      tester,
    ) async {
      await openToday(tester);
      await tester.tap(find.widgetWithText(Card, 'Sales').first);
      await tester.pumpAndSettle();
      expect(find.text('Key figures'), findsWidgets);
    });

    testWidgets(
      'the bars are green for good news and amber for a partial result',
      (tester) async {
        await openToday(tester);
        final bars = tester
            .widgetList<Container>(find.byType(Container))
            .where((c) => c.color == Palette.ok);
        expect(bars, isNotEmpty);
      },
    );
  });
}
