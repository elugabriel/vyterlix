import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/models.dart';
import 'package:vyterlix_mobile/models_insights.dart';
import 'package:vyterlix_mobile/models_plain.dart';
import 'package:vyterlix_mobile/theme.dart';
import 'package:vyterlix_mobile/widgets/plain.dart';

import 'insights_test.dart' show openBusiness, openMore;
import 'support.dart';

/// The colour of the bar beside a sentence.
Color? barColour(WidgetTester tester, String sentence) {
  final box = tester.widget<Container>(
    find
        .ancestor(of: find.text(sentence), matching: find.byType(Container))
        .first,
  );
  final decoration = box.decoration as BoxDecoration;
  return (decoration.border as Border).left.color;
}

void main() {
  group('what the server says a figure means', () {
    test('is read, with an optional caution', () {
      final p = PlainFigure.from({
        'says': 'You sold more than last month.',
        'why': 'More sales is good news.',
        'tone': 'good',
        'caution': 'Some of the data is incomplete.',
      })!;
      expect((p.says, p.tone), ('You sold more than last month.', 'good'));
      expect(p.caution, 'Some of the data is incomplete.');
      expect(PlainFigure.from(null), isNull);
      expect(PlainFigure.from('nonsense'), isNull);
    });

    test('is carried by the front-screen figures, the key figures and one figure\'s history', () {
      final d = dashboard();
      final figures = [
        for (final f in d['figures'] as List)
          FigureGlance.fromJson(f as Map<String, dynamic>),
      ];
      expect(figures[0].plain!.says, 'You sold more than last month.');
      expect(figures[2].plain, isNull); // nothing sent: nothing said
      final kpis = [
        for (final k in kpisJson()['kpis'] as List)
          Kpi.fromJson(k as Map<String, dynamic>),
      ];
      expect(kpis.first.plain!.tone, 'good');
      expect(kpis[1].plain!.caution, isNotNull);
      expect(kpis.last.plain, isNull);
      expect(
        KpiHistory.fromJson(kpiHistoryJson()).plain!.says,
        'You sold more than last month.',
      );
    });
  });

  group('on the front screen', () {
    testWidgets('each headline figure says what it means before its number', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      // (also once under "What is going well?")
      expect(find.text('You sold more than last month.'), findsNWidgets(2));
      expect(
        find.text('Your running costs are higher than last month.'),
        findsOneWidget,
      );
      // only the short sentence here; the reason is kept for the key figures page
      expect(find.text('More sales is good news.'), findsNothing);
      expect(
        tester.getTopLeft(find.text('You sold more than last month.').last).dy,
        lessThan(tester.getTopLeft(find.text('£8,717.10')).dy),
      );
    });

    testWidgets('green for good news and red for bad', (tester) async {
      await openBusiness(tester, happyServer());
      expect(barColour(tester, 'You sold more than last month.'), Palette.ok);
      expect(
        barColour(tester, 'Your running costs are higher than last month.'),
        Palette.bad,
      );
    });

    testWidgets('a figure the server said nothing about still shows', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      expect(find.text('Gross margin'), findsOneWidget);
      expect(find.text('75.6%'), findsOneWidget);
    });
  });

  group('in key figures', () {
    testWidgets('each card says what the figure means above its number', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Key figures');
      expect(find.text('You sold more than last month.'), findsOneWidget);
      expect(
        find.text('Your running costs are higher than last month.'),
        findsOneWidget,
      );
      expect(
        tester.getTopLeft(find.text('You sold more than last month.')).dy,
        lessThan(tester.getTopLeft(find.text('£8,717.10')).dy),
      );
    });

    testWidgets('a caution about incomplete data is shown', (tester) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Key figures');
      expect(
        find.text(
          'Some of the data behind this figure is incomplete, so treat it with care.',
        ),
        findsOneWidget,
      );
    });

    testWidgets(
      'a figure with nothing to say shows its number or what it needs, as before',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'Key figures');
        expect(find.text('£8,717.10'), findsOneWidget);
        expect(find.text('Not worked out yet.'), findsOneWidget);
      },
    );

    testWidgets('opening one figure leads with the sentence and the reason', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await openMore(tester, 'Key figures');
      await tester.tap(find.text('Sales').last);
      await tester.pumpAndSettle();
      expect(find.text('You sold more than last month.'), findsOneWidget);
      expect(
        find.text(
          'More sales is good news, as long as your costs did not rise just as fast.',
        ),
        findsOneWidget,
      );
      expect(
        tester.getTopLeft(find.text('You sold more than last month.')).dy,
        lessThan(tester.getTopLeft(find.text('About Sales')).dy),
      );
    });
  });

  testWidgets('the block shows the reason unless it is short', (tester) async {
    const plain = PlainFigure(
      says: 'S.',
      why: 'W.',
      tone: 'watch',
      caution: null,
    );
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: Column(
            children: [
              PlainBlock(plain: plain),
              PlainBlock(plain: plain, short: true),
            ],
          ),
        ),
      ),
    );
    expect(find.text('S.'), findsNWidgets(2));
    expect(find.text('W.'), findsOneWidget);
  });
}
