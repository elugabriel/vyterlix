import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/format.dart';
import 'package:vyterlix_mobile/models.dart';

import 'support.dart';

Future<Harness> openBusiness(
  WidgetTester tester,
  FakeServer server, {
  String name = 'Fakeham Bakery',
}) async {
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

void main() {
  group('what comes from the server', () {
    test('an alert and its history are read', () {
      final detail = AlertDetail.fromJson(alertDetailJson('x'));
      expect(detail.alert.title, 'Alert x title');
      expect(
        (detail.alert.severity, detail.alert.status, detail.alert.occurrences),
        ('high', 'open', 3),
      );
      expect(detail.alert.link, 'changes.html');
      expect(detail.events.map((e) => e.kind), ['raised', 'repeated']);
      expect(detail.events.first.user, isNull);
    });

    test('the counts and the inbox are read', () {
      final counts = AlertCounts.fromJson({
        'open': 2,
        'acknowledged': 1,
        'resolved': 5,
        'high_or_critical_open': 1,
      });
      expect(
        (counts.open, counts.acknowledged, counts.resolved, counts.seriousOpen),
        (2, 1, 5, 1),
      );
      final inbox = Inbox.fromJson({
        'unread': 2,
        'items': [notificationJson('a'), notificationJson('b', read: true)],
      });
      expect(inbox.unread, 2);
      expect(inbox.items.map((n) => n.read), [false, true]);
      final read = inbox.items.first.asRead();
      expect((read.id, read.title, read.read), ('a', 'Notification a', true));
    });

    test('a date and time is written day, month, year and 24 hour', () {
      expect(ukDateTime('2026-10-09T13:05:00'), '09/10/2026, 13:05');
      expect(ukDateTime('2027-01-02T00:07:00'), '02/01/2027, 00:07');
    });
  });

  group('the tabs along the bottom', () {
    testWidgets('are Today, Alerts and Inbox, and open on Today', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      for (final label in ['Today', 'Alerts', 'Inbox']) {
        expect(
          find.descendant(
            of: find.byType(NavigationBar),
            matching: find.text(label),
          ),
          findsOneWidget,
        );
      }
      expect(find.text('TODAY'), findsOneWidget);
    });

    testWidgets('the number of unread notifications shows on the Inbox tab', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      expect(
        find.descendant(
          of: find.byType(NavigationBar),
          matching: find.text('2'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('a business with nothing unread shows no number', (
      tester,
    ) async {
      await openBusiness(tester, happyServer(), name: 'Second Shop');
      expect(
        find.descendant(
          of: find.byType(NavigationBar),
          matching: find.byType(Badge),
        ),
        findsWidgets,
      );
      final badge = tester.widget<Badge>(
        find
            .descendant(
              of: find.byType(NavigationBar),
              matching: find.byType(Badge),
            )
            .first,
      );
      expect(badge.isLabelVisible, isFalse);
    });

    testWidgets(
      'tapping an alert that needs attention on Today goes to the Alerts tab',
      (tester) async {
        await openBusiness(tester, happyServer());
        await tester.tap(find.text('Item 0 title'));
        await tester.pumpAndSettle();
        expect(find.text('2 open, 1 of them serious.'), findsOneWidget);
      },
    );

    testWidgets('switching tabs keeps what each had loaded', (tester) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await goTo(tester, 'Alerts');
      await goTo(tester, 'Today');
      await goTo(tester, 'Alerts');
      expect(server.to('GET /organizations/b1/alerts'), hasLength(1));
    });
  });

  group('the Alerts tab', () {
    testWidgets('says how many are open and how serious, and lists them', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await goTo(tester, 'Alerts');
      expect(find.text('2 open, 1 of them serious.'), findsOneWidget);
      expect(find.text('Alert al1 title'), findsOneWidget);
      expect(find.text('Alert al2 title'), findsOneWidget);
      expect(find.text('High'), findsOneWidget);
      expect(find.text('Medium'), findsOneWidget);
      expect(
        find.text('Seen 3 times, last on 09/10/2026, 13:05'),
        findsOneWidget,
      );
      expect(find.text('Seen on 01/10/2026, 09:30'), findsOneWidget);
    });

    testWidgets('can be narrowed to those being dealt with or closed', (
      tester,
    ) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await goTo(tester, 'Alerts');
      await tester.tap(find.text('Handling 0'));
      await tester.pumpAndSettle();
      expect(
        find.text('Nothing is being dealt with right now.'),
        findsOneWidget,
      );
      await tester.tap(find.text('Closed 1'));
      await tester.pumpAndSettle();
      expect(find.text('Alert al3 title'), findsOneWidget);
      expect(find.text('Alert al1 title'), findsNothing);
      await tester.tap(find.text('Open 2'));
      await tester.pumpAndSettle();
      expect(find.text('Alert al1 title'), findsOneWidget);
      expect(
        server
            .to('GET /organizations/b1/alerts')
            .map((r) => r.url.queryParameters['status']),
        ['open', 'acknowledged', 'resolved', 'open'],
      );
    });

    testWidgets('a quiet business says so', (tester) async {
      final server = happyServer()
        ..json('GET /organizations/b1/alerts', [])
        ..json('GET /organizations/b1/alerts/summary', {
          'open': 0,
          'acknowledged': 0,
          'resolved': 0,
          'high_or_critical_open': 0,
        });
      await openBusiness(tester, server);
      await goTo(tester, 'Alerts');
      expect(find.text('Nothing is waiting for you.'), findsOneWidget);
      expect(find.text('No open alerts. Nice and quiet.'), findsOneWidget);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/alerts',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openBusiness(tester, server);
      await goTo(tester, 'Alerts');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.on(
        'GET /organizations/b1/alerts',
        (_) => jsonResponse([alertJson('al1')]),
      );
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Alert al1 title'), findsOneWidget);
    });
  });

  group('one alert', () {
    Future<void> openAlert(
      WidgetTester tester,
      FakeServer server, {
      String business = 'Fakeham Bakery',
      String title = 'Alert al1 title',
    }) async {
      await openBusiness(tester, server, name: business);
      await goTo(tester, 'Alerts');
      await tester.tap(find.text(title));
      await tester.pumpAndSettle();
    }

    testWidgets('shows what it says and what has happened, newest first', (
      tester,
    ) async {
      await openAlert(tester, happyServer());
      expect(find.text('Alert al1 title'), findsOneWidget);
      expect(find.text('Alert al1 body'), findsOneWidget);
      expect(
        find.text('Seen 3 times since 01/10/2026, 09:30.'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.text('Raised'),
        200,
        scrollable: find.byType(Scrollable).last,
      );
      expect(find.text('What has happened'), findsOneWidget);
      expect(
        tester.getTopLeft(find.text('Seen again')).dy,
        lessThan(tester.getTopLeft(find.text('Raised')).dy),
      );
    });

    testWidgets(
      'an owner can take it on, with a note, and see it in its history',
      (tester) async {
        final server = happyServer();
        await openAlert(tester, server);
        await tester.enterText(
          find.widgetWithText(TextField, 'A note (optional)'),
          '  Ringing the supplier ',
        );
        await tester.tap(find.text("I'm on it"));
        await tester.pumpAndSettle();
        expect(
          server.bodyOf(
            server.to('POST /organizations/b1/alerts/al1/acknowledge').single,
          ),
          {'note': 'Ringing the supplier'},
        );
        expect(find.text('Taken on by Jo Baker'), findsWidgets);
        expect(find.text('Ringing the supplier'), findsOneWidget);
        expect(find.text("I'm on it"), findsNothing); // it is taken on now
        expect(find.text('Close it'), findsOneWidget);
      },
    );

    testWidgets('without a note nothing is sent as one', (tester) async {
      final server = happyServer();
      await openAlert(tester, server);
      await tester.tap(find.text("I'm on it"));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/alerts/al1/acknowledge').single.body,
        isEmpty,
      );
    });

    testWidgets('closing it removes the buttons', (tester) async {
      final server = happyServer();
      await openAlert(tester, server);
      await tester.tap(find.text('Close it'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/alerts/al1/resolve'),
        hasLength(1),
      );
      expect(find.text('Close it'), findsNothing);
      expect(find.text("I'm on it"), findsNothing);
    });

    testWidgets('going back after a change refreshes the list', (tester) async {
      final server = happyServer();
      await openAlert(tester, server);
      await tester.tap(find.text('Close it'));
      await tester.pumpAndSettle();
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/alerts'), hasLength(2));
    });

    testWidgets('going back without a change does not reload the list', (
      tester,
    ) async {
      final server = happyServer();
      await openAlert(tester, server);
      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/alerts'), hasLength(1));
      expect(find.text('Alert al1 title'), findsOneWidget);
    });

    testWidgets('a refusal from the server is shown and nothing changes', (
      tester,
    ) async {
      final server = happyServer()
        ..on(
          'POST /organizations/b1/alerts/al1/acknowledge',
          (_) => errorResponse(
            403,
            'outside_remit',
            'This alert is outside your area.',
          ),
        );
      await openAlert(tester, server);
      await tester.tap(find.text("I'm on it"));
      await tester.pumpAndSettle();
      expect(find.text('This alert is outside your area.'), findsOneWidget);
      expect(find.text("I'm on it"), findsOneWidget);
    });

    testWidgets(
      'a viewer can look but is told only owners and managers can act',
      (tester) async {
        await openAlert(
          tester,
          happyServer(),
          business: 'Second Shop',
          title: 'Alert al9 title',
        );
        expect(find.text('Alert al9 body'), findsOneWidget);
        expect(find.text("I'm on it"), findsNothing);
        expect(find.text('Close it'), findsNothing);
        expect(
          find.text('Only owners and managers can take on or close an alert.'),
          findsOneWidget,
        );
      },
    );

    testWidgets('a problem loading it can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/alerts/al1',
          (_) =>
              errorResponse(404, 'alert_not_found', 'That alert was not found'),
        );
      await openAlert(tester, server);
      expect(find.text('That alert was not found'), findsOneWidget);
      server.json('GET /organizations/b1/alerts/al1', alertDetailJson('al1'));
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Alert al1 body'), findsOneWidget);
    });
  });

  group('the Inbox tab', () {
    testWidgets('lists what the person has been told, unread ones marked', (
      tester,
    ) async {
      await openBusiness(tester, happyServer());
      await goTo(tester, 'Inbox');
      expect(find.text('2 unread'), findsOneWidget);
      expect(find.text('Notification n1'), findsOneWidget);
      expect(find.text('Body of n2'), findsOneWidget);
      expect(find.byKey(const ValueKey('unread-n1')), findsOneWidget);
      expect(find.byKey(const ValueKey('read-n3')), findsOneWidget);
      expect(
        tester.widget<Text>(find.text('Notification n1')).style!.fontWeight,
        FontWeight.w700,
      );
      expect(
        tester.widget<Text>(find.text('Notification n3')).style!.fontWeight,
        FontWeight.w500,
      );
    });

    testWidgets(
      'tapping one marks it read and the number on the tab goes down',
      (tester) async {
        final server = happyServer();
        await openBusiness(tester, server);
        await goTo(tester, 'Inbox');
        await tester.tap(find.text('Notification n1'));
        await tester.pumpAndSettle();
        expect(
          server.to('POST /organizations/b1/notifications/n1/read'),
          hasLength(1),
        );
        expect(find.byKey(const ValueKey('read-n1')), findsOneWidget);
        expect(find.text('1 unread'), findsOneWidget);
        expect(
          find.descendant(
            of: find.byType(NavigationBar),
            matching: find.text('1'),
          ),
          findsOneWidget,
        );
        await tester.tap(
          find.text('Notification n1'),
        ); // already read: nothing more is sent
        await tester.pumpAndSettle();
        expect(
          server.to('POST /organizations/b1/notifications/n1/read'),
          hasLength(1),
        );
      },
    );

    testWidgets('mark all as read clears the number', (tester) async {
      final server = happyServer();
      await openBusiness(tester, server);
      await goTo(tester, 'Inbox');
      await tester.tap(find.text('Mark all as read'));
      await tester.pumpAndSettle();
      expect(
        server.to('POST /organizations/b1/notifications/read-all'),
        hasLength(1),
      );
      expect(find.text('You are up to date.'), findsOneWidget);
      expect(find.text('Mark all as read'), findsNothing);
      expect(find.byKey(const ValueKey('unread-n2')), findsNothing);
    });

    testWidgets('an empty inbox says so', (tester) async {
      await openBusiness(tester, happyServer(), name: 'Second Shop');
      await goTo(tester, 'Inbox');
      expect(find.text('Nothing here yet.'), findsOneWidget);
      expect(find.text('You are up to date.'), findsOneWidget);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = happyServer();
      await openBusiness(tester, server);
      server.on(
        'GET /organizations/b1/notifications',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
      await goTo(tester, 'Inbox');
      await tester.fling(
        find.byType(ListView).last,
        const Offset(0, 400),
        1000,
      );
      await tester.pumpAndSettle();
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/notifications', {
        'unread': 0,
        'items': [],
      });
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('You are up to date.'), findsOneWidget);
    });

    testWidgets('a failure to mark one read shows what the server now says', (
      tester,
    ) async {
      final server = happyServer()
        ..on(
          'POST /organizations/b1/notifications/n1/read',
          (_) => errorResponse(404, 'notification_not_found', 'Not found'),
        );
      await openBusiness(tester, server);
      await goTo(tester, 'Inbox');
      await tester.tap(find.text('Notification n1'));
      await tester.pumpAndSettle();
      expect(
        server.to('GET /organizations/b1/notifications').length,
        greaterThan(1),
      );
      expect(find.text('2 unread'), findsOneWidget);
    });
  });
}
