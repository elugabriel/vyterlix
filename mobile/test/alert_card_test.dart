import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models.dart';
import 'package:vyterlix_mobile/models_alert_card.dart';
import 'package:vyterlix_mobile/theme.dart';
import 'package:vyterlix_mobile/widgets/alert_card_view.dart';

import 'support.dart';

Map<String, dynamic> cardJson({
  String state = 'open',
  bool canDecide = true,
  bool withSuggestion = true,
  Map<String, dynamic>? action,
  String level = 'high',
}) => {
  'alert': alertJson('ac1', ruleCode: 'change_sales'),
  'state': state,
  'event_id': 'e1',
  'headline': 'Sales fell sharply in March 2026.',
  'happened': 'You sold less than last month.',
  'numbers':
      'Sales was £410.00, compared with £600.00 last month (down 31.7%).',
  'why_it_matters': 'Check whether this is a normal quiet spell for your business before reacting.',
  'season_note': null,
  'tone': 'bad',
  'priority': {
    'level': level,
    'label': level[0].toUpperCase() + level.substring(1),
    'reason': 'High priority: it could have a big effect on your business, it is urgent, and the evidence is strong.',
  },
  'chart': {
    'code': 'revenue',
    'name': 'Sales',
    'unit': 'gbp',
    'marked': '2026-03-01',
    'points': [
      {'period_start': '2026-01-01', 'value': '700.00', 'is_complete': true},
      {'period_start': '2026-02-01', 'value': '600.00', 'is_complete': true},
      {'period_start': '2026-03-01', 'value': '410.00', 'is_complete': true},
    ],
  },
  'evidence': ['Most of the fall is in one product: sourdough fell by £140.'],
  'suggestion': withSuggestion
      ? {
          'option_id': 'o1',
          'title': 'Promote the product that has slipped',
          'description': 'Sourdough is selling less than usual.',
          'target': 'Sourdough',
          'steps': ['Pick a simple offer', 'Tell your regulars'],
          'why_this_one': 'It answers the biggest cause we found.',
          'effort': 'low',
          'cost_level': 'low',
          'days_to_effect': 14,
          'due_by': '2026-10-29',
          'other_options': 2,
        }
      : null,
  'action': action,
  'dismissed_reason': null,
  'next_step': null,
  'can_decide': canDecide,
};

Map<String, dynamic> acceptedCard() => cardJson(
  state: 'accepted',
  canDecide: false,
  withSuggestion: false,
  action: {
    'id': 'a1',
    'title': 'Promote the product that has slipped',
    'status': 'accepted',
    'status_label': 'Accepted',
    'owner': 'Sam Lee',
    'target_date': '2026-10-29',
    'check_from': '2026-11-12',
  },
);

FakeServer cardServer([Map<String, dynamic>? card]) => happyServer()
  ..json('GET /organizations/b1/alerts', [
    alertJson('ac1', ruleCode: 'change_sales'),
  ])
  ..json(
    'GET /organizations/b1/alerts/ac1',
    alertDetailJson('ac1', ruleCode: 'change_sales'),
  )
  ..json('GET /organizations/b1/alerts/ac1/card', card ?? cardJson())
  ..json('GET /organizations/b1/members', [
    {'user_id': 'u1', 'full_name': 'Jo Baker', 'status': 'active'},
    {'user_id': 'u2', 'full_name': 'Sam Lee', 'status': 'active'},
  ])
  ..json('POST /organizations/b1/alerts/ac1/accept', acceptedCard())
  ..json('POST /organizations/b1/alerts/ac1/dismiss', {
    ...cardJson(state: 'dismissed', canDecide: false, withSuggestion: false),
    'dismissed_reason': 'Not acting on it: our quiet week',
  });

Future<void> openCard(
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
      matching: find.text('Alerts'),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  group('what comes from the server', () {
    test('the card is read with its chart, suggestion and action', () {
      final c = AlertCard.fromJson(cardJson());
      expect(c.state, 'open');
      expect(c.priority.label, 'High');
      expect(c.chart!.markedIndex, 2);
      expect(c.chart!.values, [700.0, 600.0, 410.0]);
      expect(c.suggestion!.title, 'Promote the product that has slipped');
      expect(c.suggestion!.steps, hasLength(2));
      expect(c.suggestion!.daysToEffect, 14);
      expect(c.action, isNull);
      final done = AlertCard.fromJson(acceptedCard());
      expect(done.action!.owner, 'Sam Lee');
      expect(done.action!.checkFrom, '2026-11-12');
      expect(done.suggestion, isNull);
    });

    test('an alert says whether it is about a figure that changed', () {
      expect(
        AlertSummary.fromJson(alertJson('x', ruleCode: 'change_customer'))
            .isChange,
        isTrue,
      );
      expect(AlertSummary.fromJson(alertJson('x')).isChange, isFalse);
    });

    test('a card without a chart or suggestion is still read', () {
      final json = {
        ...cardJson(),
        'chart': null,
        'suggestion': null,
        'next_step': {'text': 'Do this', 'link': null},
      };
      final c = AlertCard.fromJson(json);
      expect((c.chart, c.suggestion, c.nextStepText), (null, null, 'Do this'));
    });
  });

  group('opening an alert about a figure that changed', () {
    testWidgets(
      'shows what happened, why it matters, how urgent, and what to do',
      (tester) async {
        await openCard(tester, cardServer());
        await tester.tap(find.text('Alert ac1 title'));
        await tester.pumpAndSettle();
        expect(find.text('High priority'), findsOneWidget);
        expect(find.text('Sales fell sharply in March 2026.'), findsOneWidget);
        expect(find.text('You sold less than last month.'), findsOneWidget);
        expect(
          find.text(
            'Sales was £410.00, compared with £600.00 last month (down 31.7%).',
          ),
          findsOneWidget,
        );
        expect(
          find.textContaining('it could have a big effect on your business'),
          findsOneWidget,
        );
        expect(
          find.textContaining('The orange bar is the month this is about.'),
          findsOneWidget,
        );
        expect(
          find.text(
            'Most of the fall is in one product: sourdough fell by £140.',
          ),
          findsOneWidget,
        );
        expect(
          find.text('Promote the product that has slipped'),
          findsOneWidget,
        );
        expect(find.text('Accept'), findsOneWidget);
        for (final b in ['Edit', 'Assign', 'Dismiss']) {
          expect(find.text(b), findsOneWidget);
        }
        expect(
          find.textContaining('assigned to you, due by 29/10/2026'),
          findsOneWidget,
        );
      },
    );

    testWidgets('the old "I\'m on it" buttons are not shown for it', (
      tester,
    ) async {
      await openCard(tester, cardServer());
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      expect(find.text("I'm on it"), findsNothing);
      expect(find.text('Close it'), findsNothing);
      expect(find.text('What has happened'), findsOneWidget);
    });

    testWidgets('the priority colour follows the level', (tester) async {
      expect(priorityColor('high'), Palette.bad);
      expect(priorityColor('medium'), Palette.warn);
      expect(priorityColor('low'), Palette.lightMuted);
    });
  });

  group('deciding', () {
    testWidgets('Accept takes it as suggested and shows the action', (
      tester,
    ) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Accept'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/alerts/ac1/accept').single,
        ),
        isEmpty,
      );
      expect(
        find.text('You accepted this, so it is now an action.'),
        findsOneWidget,
      );
      expect(
        find.text('Assigned to Sam Lee, due by 29/10/2026.'),
        findsOneWidget,
      );
      expect(
        find.text('We will check how it went from 12/11/2026.'),
        findsOneWidget,
      );
      expect(find.text('Accept'), findsNothing);
    });

    testWidgets('Edit sends the changed words and steps', (tester) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'What to do'),
        'Ring the regulars',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Steps (one on each line)'),
        'Make a list\n\nCall them',
      );
      await tester.tap(find.text('Accept with my changes'));
      await tester.pumpAndSettle();
      final body = server.bodyOf(
        server.to('POST /organizations/b1/alerts/ac1/accept').single,
      );
      expect(body['title'], 'Ring the regulars');
      expect(body['steps'], [
        {'text': 'Make a list'},
        {'text': 'Call them'},
      ]);
      expect(
        find.text('You accepted this, so it is now an action.'),
        findsOneWidget,
      );
    });

    testWidgets('Assign sends the person and the date', (tester) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Assign'));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('assign-owner')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Sam Lee').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Accept and assign'));
      await tester.pumpAndSettle();
      final body = server.bodyOf(
        server.to('POST /organizations/b1/alerts/ac1/accept').single,
      );
      expect(body['owner_user_id'], 'u2');
      expect(body['target_date'], '2026-10-29');
    });

    testWidgets('Assign leaves the owner out when it stays with me', (
      tester,
    ) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Assign'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Accept and assign'));
      await tester.pumpAndSettle();
      final body = server.bodyOf(
        server.to('POST /organizations/b1/alerts/ac1/accept').single,
      );
      expect(body.containsKey('owner_user_id'), isFalse);
    });

    testWidgets('Dismiss keeps the reason and shows it', (tester) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Dismiss'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, 'our quiet week');
      await tester.tap(find.text('Dismiss it'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/alerts/ac1/dismiss').single,
        ),
        {'reason': 'our quiet week'},
      );
      expect(
        find.textContaining('You decided not to act on this.'),
        findsOneWidget,
      );
      expect(find.textContaining('our quiet week'), findsOneWidget);
    });

    testWidgets('Cancel in a form sends nothing', (tester) async {
      final server = cardServer();
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/alerts/ac1/accept'), isEmpty);
      expect(find.text('Accept'), findsOneWidget);
    });

    testWidgets('a refusal is shown and the card stays', (tester) async {
      final server = cardServer()
        ..on(
          'POST /organizations/b1/alerts/ac1/accept',
          (_) => errorResponse(
            409,
            'not_open',
            'This recommendation has already been dealt with.',
          ),
        );
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Accept'));
      await tester.pumpAndSettle();
      expect(
        find.text('This recommendation has already been dealt with.'),
        findsOneWidget,
      );
      expect(find.text('Accept'), findsOneWidget);
    });
  });

  group('the other states of the card', () {
    Future<void> show(WidgetTester tester, Map<String, dynamic> card) async {
      await openCard(tester, cardServer(card));
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
    }

    testWidgets('someone who may only look is told so, with no buttons', (
      tester,
    ) async {
      await show(tester, cardJson(canDecide: false));
      expect(
        find.text('Only an owner or manager can accept or dismiss this.'),
        findsOneWidget,
      );
      expect(find.text('Accept'), findsNothing);
      expect(find.text('Promote the product that has slipped'), findsOneWidget);
    });

    testWidgets('good news says there is nothing to put right', (tester) async {
      await show(
        tester,
        cardJson(
          state: 'good_news',
          canDecide: false,
          withSuggestion: false,
          level: 'low',
        ),
      );
      expect(find.text('Good news'), findsOneWidget);
      expect(find.text('Low priority'), findsOneWidget);
      expect(find.text('Accept'), findsNothing);
    });

    testWidgets('no suggestion yet says so', (tester) async {
      await show(
        tester,
        cardJson(
          state: 'no_suggestion',
          canDecide: false,
          withSuggestion: false,
        ),
      );
      expect(
        find.textContaining('We cannot say what to do about this yet.'),
        findsOneWidget,
      );
    });

    testWidgets(
      'a suggestion not made yet is explained to someone who cannot ask for one',
      (tester) async {
        await show(
          tester,
          cardJson(state: 'not_yet', canDecide: false, withSuggestion: false),
        );
        expect(
          find.textContaining('A suggestion has not been made yet.'),
          findsOneWidget,
        );
      },
    );

    testWidgets('one put forward for approval says it is waiting', (
      tester,
    ) async {
      await show(
        tester,
        cardJson(
          state: 'proposed',
          canDecide: false,
          action: {
            'id': 'a1',
            'title': 'Ring them',
            'status': 'pending',
            'status_label': 'Waiting for approval',
            'owner': null,
            'target_date': null,
            'check_from': null,
          },
        ),
      );
      expect(find.textContaining('Put forward for approval.'), findsOneWidget);
      expect(find.text('Assigned to nobody yet.'), findsOneWidget);
    });

    testWidgets('a failed load can be tried again', (tester) async {
      final server = cardServer()
        ..on(
          'GET /organizations/b1/alerts/ac1/card',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openCard(tester, server);
      await tester.tap(find.text('Alert ac1 title'));
      await tester.pumpAndSettle();
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/alerts/ac1/card', cardJson());
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Accept'), findsOneWidget);
    });
  });

  group('from the front screen', () {
    testWidgets('an alert about a figure that changed opens its card', (
      tester,
    ) async {
      final server = cardServer();
      final d = dashboard();
      (d['attention'] as List)[0] = {
        'id': 'alert:ac1',
        'kind': 'alert',
        'severity': 'high',
        'title': 'Item 0 title',
        'detail': 'Item 0 detail',
        'link': 'alerts.html#ac1',
        'category': 'sales',
        'can_act': true,
      };
      server.json('GET /organizations/b1/dashboard', d);
      tester.view.physicalSize = const Size(800, 3000);
      tester.view.devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);
      await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await tester.tap(find.text('Fakeham Bakery'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Item 0 title'));
      await tester.pumpAndSettle();
      expect(find.text('Sales fell sharply in March 2026.'), findsOneWidget);
      expect(find.text('Accept'), findsOneWidget);
    });
  });
}
