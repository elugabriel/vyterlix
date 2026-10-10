import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models_actions.dart';

import 'support.dart';

Future<Harness> openBusiness(
  WidgetTester tester,
  FakeServer server, {
  String name = 'Fakeham Bakery',
}) async {
  // A tall screen, so everything on a page is built without scrolling to it.
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

Future<void> goTo(WidgetTester tester, String tab) async {
  await tester.tap(
    find.descendant(of: find.byType(NavigationBar), matching: find.text(tab)),
  );
  await tester.pumpAndSettle();
}

Future<void> chip(WidgetTester tester, String label) async {
  final finder = find.widgetWithText(ChoiceChip, label);
  await tester.ensureVisible(finder);
  await tester.pumpAndSettle();
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

/// Scroll the open page (not a text box inside it) until the thing is on screen.
Future<void> scrollTo(WidgetTester tester, Finder finder) =>
    tester.scrollUntilVisible(
      finder,
      200,
      scrollable: find
          .descendant(
            of: find.byType(ListView).last,
            matching: find.byType(Scrollable),
          )
          .first,
    );

void main() {
  ideaNotWorkedOut();
  group('what comes from the server', () {
    test('an action in full is read', () {
      final d = ActionDetail.fromJson(
        actionDetailJson(
          'x',
          followUp: {
            'due_date': '2026-11-14',
            'measure_month': '2026-10-01',
            'status': 'scheduled',
            'is_due': false,
          },
        ),
      );
      expect(
        (d.status, d.statusLabel, d.owner!.name),
        ('in_progress', 'In progress', 'Jo Baker'),
      );
      expect(d.nextStatuses, ['partially_completed', 'completed', 'cancelled']);
      expect(d.steps.map((s) => s.done), [true, false, false]);
      expect(
        (d.progress.done, d.progress.total, d.progress.percent),
        (1, 3, 33),
      );
      expect(d.decision.kpiName, 'Sales');
      expect(d.decision.baselineValue, '8717.1');
      expect(d.updates.map((u) => u.kind), ['created', 'overdue']);
      expect(d.updates.last.user, isNull);
      expect(d.followUp!.status, 'scheduled');
      expect(d.outcome, isNull);
    });

    test('an action with a result is read', () {
      final d = ActionDetail.fromJson(
        actionDetailJson(
          'x',
          status: 'completed',
          next: [],
          outcome: {
            'outcome': 'successful',
            'label': 'It worked',
            'reason': 'r',
            'kpi_name': 'Sales',
            'unit': 'gbp',
            'baseline_value': '1',
            'measured_value': '2',
            'achieved_pct': 150,
            'alternative': null, //
          },
        ),
      );
      expect(d.outcome!.label, 'It worked');
      expect(d.outcome!.achievedPct, 150);
      expect(d.nextStatuses, isEmpty);
    });

    test('a summary, the counts and a recommendation are read', () {
      final s = ActionSummary.fromJson(
        actionSummaryJson('x', daysLate: 5, status: 'overdue', owner: null),
      );
      expect(
        (s.status, s.daysLate, s.owner, s.progress.percent),
        ('overdue', 5, null, 33),
      );
      final c = ActionCounts.fromJson({
        'open': 3,
        'overdue': 1,
        'due_soon': 2,
        'mine_open': 1,
        'pending': 1,
      });
      expect(
        (c.open, c.overdue, c.dueSoon, c.mineOpen, c.pending),
        (3, 1, 2, 1, 1),
      );
      final r = RecommendationDetail.fromJson(recommendationJson());
      expect(r.options.map((o) => o.rank), [1, 2]);
      expect(r.options.first.isRecommended, isTrue);
      expect(r.options.first.steps, ['First step of o1', 'Second step of o1']);
      expect(
        (
          r.options.first.effort,
          r.options.first.costLevel,
          r.options.first.daysToEffect,
        ),
        ('low', 'none', 14),
      );
    });
  });

  group('the Actions tab', () {
    testWidgets(
      'says how much is still to do and lists it with who and by when',
      (tester) async {
        await openBusiness(tester, happyServer());
        await goTo(tester, 'Actions');
        expect(find.text('3 still to do, 1 late, 1 due soon.'), findsOneWidget);
        expect(find.text('Action a1 title'), findsOneWidget);
        expect(find.text('Jo Baker · Due 31/10/2026'), findsWidgets);
        expect(
          find.text('Jo Baker · 5 days late (was due 04/10/2026)'),
          findsOneWidget,
        );
        expect(find.text('Overdue'), findsOneWidget);
        expect(find.text('1 of 3 steps'), findsOneWidget);
      },
    );

    testWidgets('each list asks the server for the right thing', (
      tester,
    ) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await goTo(tester, 'Actions');
      await chip(tester, 'Mine');
      await chip(tester, 'Waiting for approval');
      await chip(tester, 'Done');
      final asked = server
          .to('GET /organizations/b1/actions')
          .map((r) => r.url.queryParameters)
          .toList();
      expect(asked, [
        {'open_only': 'true'},
        {'mine': 'true', 'open_only': 'true'},
        {'status': 'pending'},
        {'status': 'completed'},
      ]);
      expect(find.text('Action a3 title'), findsOneWidget);
    });

    testWidgets('an action nobody has been given is said so', (tester) async {
      await openBusiness(tester, happyServer());
      await goTo(tester, 'Actions');
      await chip(tester, 'Waiting for approval');
      expect(find.text('Waiting for approval'), findsWidgets);
      expect(
        find.text('Not given to anyone yet · Due 31/10/2026'),
        findsOneWidget,
      );
    });

    testWidgets('empty lists say what that means', (tester) async {
      final server = happyServer()
        ..json('GET /organizations/b1/actions', [])
        ..json('GET /organizations/b1/recommendations', []);
      await openBusiness(tester, server);
      await goTo(tester, 'Actions');
      expect(
        find.text('No actions here. Take up an idea to start one.'),
        findsOneWidget,
      );
      await chip(tester, 'Mine');
      expect(find.text('Nothing is assigned to you.'), findsOneWidget);
      await chip(tester, 'Waiting for approval');
      expect(find.text('Nothing is waiting for approval.'), findsOneWidget);
      await chip(tester, 'Done');
      expect(find.text('Nothing has been finished yet.'), findsOneWidget);
      await chip(tester, 'Ideas');
      expect(
        find.text('No ideas right now. They appear when a figure moves.'),
        findsOneWidget,
      );
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/actions',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await goTo(tester, 'Actions');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.on(
        'GET /organizations/b1/actions',
        (_) => jsonResponse([actionSummaryJson('a1')]),
      );
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Action a1 title'), findsOneWidget);
    });

    testWidgets('the ideas list shows what Vyterlix suggests', (tester) async {
      await openBusiness(tester, happyServer());
      await goTo(tester, 'Actions');
      await chip(tester, 'Ideas');
      expect(find.text('Sales fell 11.9% in September'), findsOneWidget);
      expect(find.text('Suggested: Re-price o1'), findsOneWidget);
      expect(find.text('Sales · September 2026'), findsOneWidget);
    });
  });

  group('from the Today screen', () {
    Future<void> tapOnToday(
      WidgetTester tester,
      String kind,
      String title,
    ) async {
      final data = dashboard();
      (data['attention'] as List)[0] = {
        'id': 'x',
        'kind': kind,
        'severity': 'high',
        'title': title,
        'detail': 'd',
        'link': 'actions.html',
        'category': null,
        'can_act': true,
      };
      final server = happyServer()
        ..json('GET /organizations/b1/dashboard', data);
      final h = await openBusiness(tester, server);
      await tester.tap(find.text(title));
      await tester.pumpAndSettle();
      expect(h.auth.status.name, 'signedIn');
    }

    testWidgets('something waiting for approval opens that list', (
      tester,
    ) async {
      await tapOnToday(tester, 'approval', 'Please approve this');
      expect(
        find.widgetWithText(ChoiceChip, 'Waiting for approval'),
        findsOneWidget,
      );
      expect(
        tester
            .widget<ChoiceChip>(
              find.widgetWithText(ChoiceChip, 'Waiting for approval'),
            )
            .selected,
        isTrue,
      );
      expect(find.text('Action a2 title'), findsOneWidget);
    });

    testWidgets('a suggestion opens the ideas', (tester) async {
      await tapOnToday(tester, 'suggestion', 'Try this');
      expect(
        tester
            .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'Ideas'))
            .selected,
        isTrue,
      );
      expect(find.text('Sales fell 11.9% in September'), findsOneWidget);
    });

    testWidgets('late work opens what is still to do', (tester) async {
      await tapOnToday(tester, 'action_overdue', 'This is late');
      expect(
        tester
            .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'Still to do'))
            .selected,
        isTrue,
      );
      expect(find.text('Action a4 title'), findsOneWidget);
    });
  });

  group('one action', () {
    Future<FakeServer> openAction(
      WidgetTester tester, {
      String id = 'a1',
      String business = 'Fakeham Bakery',
      FakeServer? server,
      String? list,
    }) async {
      final s = server ?? happyServer();
      await openBusiness(tester, s, name: business);
      await goTo(tester, 'Actions');
      if (list != null) await chip(tester, list);
      await tester.tap(find.text('Action $id title'));
      await tester.pumpAndSettle();
      return s;
    }

    testWidgets(
      'shows what it is, who is doing it, the steps and what was decided',
      (tester) async {
        await openAction(tester);
        expect(find.text('In progress'), findsOneWidget);
        expect(find.text('Action a1 description'), findsOneWidget);
        expect(find.text('Jo Baker'), findsWidgets);
        expect(find.text('01/10/2026'), findsOneWidget);
        expect(find.text('31/10/2026'), findsOneWidget);
        expect(find.text('1 of 3 done'), findsOneWidget);
        expect(find.text('Ring the supplier'), findsOneWidget);
        expect(
          tester
              .widget<CheckboxListTile>(
                find.widgetWithText(CheckboxListTile, 'Ring the supplier'),
              )
              .value,
          isTrue,
        );
        await scrollTo(tester, find.text('Aimed at Sales'));
        expect(
          find.text('It was £8,717.10 in September 2026.'),
          findsOneWidget,
        );
        expect(
          find.text('We expected it to improve by about £450.00.'),
          findsOneWidget,
        );
        expect(find.text('About: Sourdough'), findsOneWidget);
      },
    );

    testWidgets('ticking a step sends all the steps with that one changed', (
      tester,
    ) async {
      final server = await openAction(tester);
      await tester.tap(find.text('Change the order'));
      await tester.pumpAndSettle();
      final sent = server.bodyOf(
        server.to('PATCH /organizations/b1/actions/a1').single,
      );
      expect(sent['steps'], [
        {'text': 'Ring the supplier', 'done': true},
        {'text': 'Change the order', 'done': true},
        {'text': 'Tell the team', 'done': false},
      ]);
      expect(find.text('2 of 3 done'), findsOneWidget);
    });

    testWidgets(
      'moving it on uses the same words as the website and sends the note',
      (tester) async {
        final server = await openAction(tester);
        await scrollTo(tester, find.text('What do you want to do?'));
        expect(find.text('Partly done'), findsOneWidget);
        expect(find.text('Mark as done'), findsOneWidget);
        expect(find.text('Cancel it'), findsOneWidget);
        await tester.enterText(
          find.widgetWithText(TextField, 'A note (optional)'),
          ' All finished ',
        );
        await tester.tap(find.text('Mark as done'));
        await tester.pumpAndSettle();
        expect(
          server.bodyOf(
            server.to('POST /organizations/b1/actions/a1/status').single,
          ),
          {'status': 'completed', 'note': 'All finished'},
        );
        expect(find.text('Done'), findsWidgets);
        expect(find.text('Mark as done'), findsNothing); // it is finished now
        await scrollTo(tester, find.text('How it went'));
        expect(
          find.text(
            'We will check how this went on 14/11/2026, using October 2026.',
          ),
          findsOneWidget,
        );
      },
    );

    testWidgets('a note can be added without moving it', (tester) async {
      final server = await openAction(tester);
      await scrollTo(tester, find.text('What do you want to do?'));
      await tester.enterText(
        find.widgetWithText(TextField, 'A note (optional)'),
        'Spoke to the baker',
      );
      await tester.tap(find.text('Just add the note'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/actions/a1/notes').single,
        ),
        {'note': 'Spoke to the baker'},
      );
      await scrollTo(tester, find.text('Spoke to the baker'));
      expect(find.text('Spoke to the baker'), findsOneWidget);
    });

    testWidgets('adding an empty note sends nothing', (tester) async {
      final server = await openAction(tester);
      await scrollTo(tester, find.text('Just add the note'));
      await tester.tap(find.text('Just add the note'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/actions/a1/notes'), isEmpty);
    });

    testWidgets('its history is newest first and says when Vyterlix did it', (
      tester,
    ) async {
      await openAction(tester);
      await scrollTo(tester, find.text('Started · Jo Baker'));
      expect(
        tester.getTopLeft(find.text('Overdue · Vyterlix')).dy,
        lessThan(tester.getTopLeft(find.text('Started · Jo Baker')).dy),
      );
      expect(find.text('Past its date'), findsOneWidget);
    });

    testWidgets('a proposal waiting for approval can be approved', (
      tester,
    ) async {
      final server = await openAction(
        tester,
        id: 'a2',
        list: 'Waiting for approval',
      );
      await scrollTo(tester, find.text('What do you want to do?'));
      expect(find.text('Start it'), findsNothing);
      await tester.tap(find.text('Approve and accept'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/actions/a2/approve'),
        hasLength(1),
      );
      expect(find.text('Accepted'), findsOneWidget);
    });

    testWidgets('a proposal can be turned down with a reason, or kept', (
      tester,
    ) async {
      final server = await openAction(
        tester,
        id: 'a2',
        list: 'Waiting for approval',
      );
      await scrollTo(tester, find.text('Turn it down'));
      await tester.tap(find.text('Turn it down'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Keep it'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/actions/a2/reject'), isEmpty);
      await tester.tap(find.text('Turn it down'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Why? (optional)'),
        'Not now',
      );
      await tester.tap(find.widgetWithText(FilledButton, 'Turn it down'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/actions/a2/reject').single,
        ),
        {'reason': 'Not now'},
      );
      expect(find.text('Cancelled'), findsOneWidget);
    });

    testWidgets('turning one down without a reason sends none', (tester) async {
      final server = await openAction(
        tester,
        id: 'a2',
        list: 'Waiting for approval',
      );
      await scrollTo(tester, find.text('Turn it down'));
      await tester.tap(find.text('Turn it down'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Turn it down'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/actions/a2/reject').single.body,
        isEmpty,
      );
    });

    testWidgets('a finished action shows how it went and cannot be changed', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await goTo(tester, 'Actions');
      await chip(tester, 'Done');
      await tester.tap(find.text('Action a3 title'));
      await tester.pumpAndSettle();
      await scrollTo(tester, find.text('How it went'));
      expect(find.text('It worked'), findsOneWidget);
      expect(find.text('Sales rose by more than hoped.'), findsOneWidget);
      expect(
        find.text('Sales: £8,717.10 before, £9,400.00 after.'),
        findsOneWidget,
      );
      expect(find.text('152% of what was expected.'), findsOneWidget);
      expect(find.text('What do you want to do?'), findsNothing);
      expect(
        tester
            .widget<CheckboxListTile>(
              find.widgetWithText(CheckboxListTile, 'Ring the supplier'),
            )
            .onChanged,
        isNull,
      );
    });

    testWidgets('a viewer can look but is told they cannot change it', (
      tester,
    ) async {
      await openAction(tester, id: 'a9', business: 'Second Shop');
      await scrollTo(
        tester,
        find.text('Only owners and managers can change an action.'),
      );
      expect(
        find.text('Only owners and managers can change an action.'),
        findsOneWidget,
      );
      expect(find.text('Start it'), findsNothing);
      expect(
        tester
            .widget<CheckboxListTile>(
              find.widgetWithText(CheckboxListTile, 'Ring the supplier'),
            )
            .onChanged,
        isNull,
      );
    });

    testWidgets('a refusal from the server is shown and nothing changes', (
      tester,
    ) async {
      final server = happyServer()
        ..on(
          'POST /organizations/b1/actions/a1/status',
          (_) => errorResponse(
            403,
            'outside_remit',
            'This action is outside your area.',
          ),
        );
      await openAction(tester, server: server);
      await scrollTo(tester, find.text('Mark as done'));
      await tester.tap(find.text('Mark as done'));
      await tester.pumpAndSettle();
      expect(find.text('This action is outside your area.'), findsOneWidget);
      expect(find.text('Mark as done'), findsOneWidget);
    });

    testWidgets('a problem loading it can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/actions/a1',
          (_) => errorResponse(
            404,
            'action_not_found',
            'That action was not found',
          ),
        );
      await openAction(tester, server: server);
      expect(find.text('That action was not found'), findsOneWidget);
      server.json('GET /organizations/b1/actions/a1', actionDetailJson('a1'));
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Action a1 description'), findsOneWidget);
    });

    testWidgets('going back refreshes the list', (tester) async {
      final server = await openAction(tester);
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/actions'), hasLength(2));
    });
  });

  group('an idea', () {
    Future<FakeServer> openIdea(
      WidgetTester tester, {
      String business = 'Fakeham Bakery',
      FakeServer? server,
    }) async {
      final s = server ?? happyServer();
      await openBusiness(tester, s, name: business);
      await goTo(tester, 'Actions');
      await chip(tester, 'Ideas');
      await tester.tap(find.text('Sales fell 11.9% in September'));
      await tester.pumpAndSettle();
      return s;
    }

    testWidgets('shows why this one was chosen and the options, best first', (
      tester,
    ) async {
      await openIdea(tester);
      expect(
        find.text('The best fit is to re-price the weakest line.'),
        findsOneWidget,
      );
      expect(find.text('Re-price o1'), findsOneWidget);
      expect(find.text('Recommended'), findsOneWidget);
      expect(find.text('Low effort'), findsOneWidget);
      expect(find.text('Costs nothing'), findsOneWidget);
      expect(find.text('Shows in about 14 days'), findsOneWidget);
      expect(
        find.text('Could win back about £450.00. Score 82 out of 100.'),
        findsOneWidget,
      );
      expect(find.text('• First step of o1'), findsOneWidget);
      await scrollTo(tester, find.text('Promote o2'));
      expect(find.text('A lot of effort'), findsOneWidget);
      expect(
        tester.getTopLeft(find.text('Re-price o1')).dy,
        lessThan(tester.getTopLeft(find.text('Promote o2')).dy),
      );
    });

    testWidgets('taking an option up makes an action and shows it', (
      tester,
    ) async {
      final server = await openIdea(tester);
      await scrollTo(tester, find.text('Promote o2'));
      await tester.tap(find.widgetWithText(OutlinedButton, 'Take this one up'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server
              .to('POST /organizations/b1/changes/ev1/recommendation/accept')
              .single,
        ),
        {'option_id': 'o2'},
      );
      expect(find.text('Action'), findsOneWidget);
      expect(find.text('Action a1 title'), findsOneWidget);
    });

    testWidgets('the recommended one is taken up with its own id', (
      tester,
    ) async {
      final server = await openIdea(tester);
      await tester.tap(find.widgetWithText(FilledButton, 'Take this one up'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server
              .to('POST /organizations/b1/changes/ev1/recommendation/accept')
              .single,
        ),
        {'option_id': 'o1'},
      );
    });

    testWidgets(
      'it can be left for now, with a reason, and the list is refreshed',
      (tester) async {
        final server = await openIdea(tester);
        await scrollTo(tester, find.text('Not now'));
        await tester.tap(find.text('Not now'));
        await tester.pumpAndSettle();
        await tester.enterText(
          find.widgetWithText(TextField, 'Why not? (optional)'),
          'Not this month',
        );
        await tester.tap(find.widgetWithText(FilledButton, 'Leave it'));
        await tester.pumpAndSettle();
        expect(
          server.bodyOf(
            server
                .to('POST /organizations/b1/changes/ev1/recommendation/dismiss')
                .single,
          ),
          {'reason': 'Not this month'},
        );
        expect(find.text('Idea'), findsWidgets);
        expect(
          server.to('GET /organizations/b1/recommendations'),
          hasLength(2),
        );
      },
    );

    testWidgets(
      'leaving it without a reason sends none, and changing your mind sends nothing',
      (tester) async {
        final server = await openIdea(tester);
        await scrollTo(tester, find.text('Not now'));
        await tester.tap(find.text('Not now'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Keep it'));
        await tester.pumpAndSettle();
        expect(
          server.to(
            'POST /organizations/b1/changes/ev1/recommendation/dismiss',
          ),
          isEmpty,
        );
        await tester.tap(find.text('Not now'));
        await tester.pumpAndSettle();
        await tester.tap(find.widgetWithText(FilledButton, 'Leave it'));
        await tester.pumpAndSettle();
        expect(
          server
              .to('POST /organizations/b1/changes/ev1/recommendation/dismiss')
              .single
              .body,
          isEmpty,
        );
      },
    );

    testWidgets('a viewer can read it but not take it up', (tester) async {
      final server = happyServer()
        ..json('GET /organizations/b2/recommendations', [
          {
            'id': 'rec1',
            'event_id': 'ev1',
            'kpi_name': 'Sales',
            'period_start': '2026-09-01',
            'status': 'open',
            'headline': 'Sales fell 11.9% in September',
            'recommended': 'Re-price o1',
            'score': 82,
            'generated_at': 'x',
          },
        ]);
      await openIdea(tester, business: 'Second Shop', server: server);
      expect(find.text('Re-price o1'), findsOneWidget);
      expect(find.text('Take this one up'), findsNothing);
      await scrollTo(
        tester,
        find.text('Only owners and managers can take an idea up.'),
      );
      expect(
        find.text('Only owners and managers can take an idea up.'),
        findsOneWidget,
      );
    });

    testWidgets('one already taken up says so and offers nothing', (
      tester,
    ) async {
      final server = happyServer()
        ..json(
          'GET /organizations/b1/changes/ev1/recommendation',
          recommendationJson(status: 'accepted'),
        );
      await openIdea(tester, server: server);
      expect(find.text('Take this one up'), findsNothing);
      await scrollTo(tester, find.text('This has been taken up.'));
      expect(find.text('This has been taken up.'), findsOneWidget);
    });

    testWidgets('a refusal when taking it up is shown', (tester) async {
      final server = happyServer()
        ..on(
          'POST /organizations/b1/changes/ev1/recommendation/accept',
          (_) => errorResponse(
            409,
            'already_accepted',
            'This idea has already been taken up.',
          ),
        );
      await openIdea(tester, server: server);
      await tester.tap(find.widgetWithText(FilledButton, 'Take this one up'));
      await tester.pumpAndSettle();
      expect(find.text('This idea has already been taken up.'), findsOneWidget);
      expect(find.text('Idea'), findsWidgets);
    });

    testWidgets('a problem loading it can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/changes/ev1/recommendation',
          (_) => errorResponse(500, 'internal_error', 'No recommendation yet'),
        );
      await openIdea(tester, server: server);
      expect(find.text('No recommendation yet'), findsOneWidget);
      server.json(
        'GET /organizations/b1/changes/ev1/recommendation',
        recommendationJson(),
      );
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Re-price o1'), findsOneWidget);
    });
  });
}

void ideaNotWorkedOut() {
  group('an idea that has not been worked out yet', () {
    testWidgets('says so and an owner can work it out', (tester) async {
      var made = false;
      final server = happyServer()
        ..on('GET /organizations/b1/changes/ev1/recommendation', (_) {
          return made
              ? jsonResponse(recommendationJson())
              : errorResponse(
                  404,
                  'recommendation_not_found',
                  'Not worked out yet',
                );
        })
        ..on('POST /organizations/b1/changes/ev1/recommendation', (_) {
          made = true;
          return jsonResponse(recommendationJson());
        });
      await openBusiness(tester, server);
      await goTo(tester, 'Actions');
      await chip(tester, 'Ideas');
      await tester.tap(find.text('Sales fell 11.9% in September'));
      await tester.pumpAndSettle();
      expect(
        find.text('No ideas have been worked out for this change yet.'),
        findsOneWidget,
      );
      await tester.tap(find.text('Work out what to do'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/changes/ev1/recommendation'),
        hasLength(1),
      );
      expect(find.text('Re-price o1'), findsOneWidget);
    });

    testWidgets('a viewer is only told', (tester) async {
      final server = happyServer()
        ..json('GET /organizations/b2/recommendations', [
          {
            'id': 'rec1',
            'event_id': 'ev1',
            'kpi_name': 'Sales',
            'period_start': '2026-09-01',
            'status': 'open',
            'headline': 'Sales fell 11.9% in September',
            'recommended': null,
            'score': null,
            'generated_at': 'x',
          },
        ])
        ..on(
          'GET /organizations/b2/changes/ev1/recommendation',
          (_) => errorResponse(
            404,
            'recommendation_not_found',
            'Not worked out yet',
          ),
        );
      await openBusiness(tester, server, name: 'Second Shop');
      await goTo(tester, 'Actions');
      await chip(tester, 'Ideas');
      await tester.tap(find.text('Sales fell 11.9% in September'));
      await tester.pumpAndSettle();
      expect(
        find.text('No ideas have been worked out for this change yet.'),
        findsOneWidget,
      );
      expect(find.text('Work out what to do'), findsNothing);
    });
  });
}
