import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/auth/auth_controller.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';

import 'support.dart';

Finder sheetField() => find.descendant(
  of: find.byType(BottomSheet),
  matching: find.byType(TextField),
);

void main() {
  group('the log in screen', () {
    testWidgets('shows the form when nobody is signed in', (tester) async {
      await pumpApp(tester, happyServer());
      expect(find.text('Welcome back'), findsOneWidget);
      expect(
        find.widgetWithText(TextFormField, 'Email address'),
        findsOneWidget,
      );
      expect(find.widgetWithText(TextFormField, 'Password'), findsOneWidget);
      expect(find.text('Forgot password?'), findsOneWidget);
    });

    testWidgets('asks for both fields before it calls the server', (
      tester,
    ) async {
      final server = happyServer();
      await pumpApp(tester, server);
      await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
      await tester.pumpAndSettle();
      expect(find.text('Enter your email address'), findsOneWidget);
      expect(find.text('Enter your password'), findsOneWidget);
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Email address'),
        'not an email',
      );
      await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
      await tester.pumpAndSettle();
      expect(find.text('Enter your email address'), findsOneWidget);
      expect(server.to('POST /auth/login'), isEmpty);
    });

    testWidgets('the password can be shown and hidden', (tester) async {
      await pumpApp(tester, happyServer());
      EditableText field() => tester.widget<EditableText>(
        find.descendant(
          of: find.widgetWithText(TextFormField, 'Password'),
          matching: find.byType(EditableText),
        ),
      );
      expect(field().obscureText, isTrue);
      await tester.tap(find.byTooltip('Show password'));
      await tester.pump();
      expect(field().obscureText, isFalse);
      expect(find.byTooltip('Hide password'), findsOneWidget);
    });

    testWidgets(
      'a wrong password is explained on the screen and the person can try again',
      (tester) async {
        final server = happyServer()
          ..on(
            'POST /auth/login',
            (_) => errorResponse(
              401,
              'invalid_credentials',
              'Incorrect email or password',
            ),
          );
        await pumpApp(tester, server);
        await logIn(tester, password: 'wrong');
        expect(find.text('Incorrect email or password'), findsOneWidget);
        expect(find.text('Welcome back'), findsOneWidget);
        server.json('POST /auth/login', tokens());
        await logIn(tester);
        expect(find.text('Incorrect email or password'), findsNothing);
        expect(find.text('Your businesses'), findsOneWidget);
      },
    );

    testWidgets('an app that is too old is told to update', (tester) async {
      final server = happyServer()
        ..on(
          'POST /auth/login',
          (_) => errorResponse(
            426,
            'app_update_required',
            'This version of Vyterlix is no longer supported. Please update the app to sign in.',
          ),
        );
      await pumpApp(tester, server);
      await logIn(tester);
      expect(
        find.textContaining('Please update the app to sign in'),
        findsOneWidget,
      );
    });

    testWidgets('no connection is explained', (tester) async {
      final server = happyServer();
      await pumpApp(tester, server);
      server.down = true;
      await logIn(tester);
      expect(find.textContaining('Could not reach Vyterlix'), findsOneWidget);
    });

    testWidgets('forgot password sends a reset email and says so', (
      tester,
    ) async {
      final server = happyServer();
      await pumpApp(tester, server);
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Email address'),
        'jo@example.co.uk',
      );
      await tester.tap(find.text('Forgot password?'));
      await tester.pumpAndSettle();
      expect(find.text('Reset your password'), findsOneWidget);
      expect(sheetField(), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, 'Send me the link'));
      await tester.pumpAndSettle();
      expect(server.bodyOf(server.to('POST /auth/forgot-password').single), {
        'email': 'jo@example.co.uk',
      });
      expect(find.textContaining('we have sent a link'), findsOneWidget);
      expect(find.text('Reset your password'), findsNothing);
    });

    testWidgets('forgot password needs an email address', (tester) async {
      final server = happyServer();
      await pumpApp(tester, server);
      await tester.tap(find.text('Forgot password?'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, 'Send me the link'));
      await tester.pumpAndSettle();
      expect(find.text('Enter your email address'), findsOneWidget);
      expect(server.to('POST /auth/forgot-password'), isEmpty);
    });

    testWidgets('forgot password shows a server problem and stays open', (
      tester,
    ) async {
      final server = happyServer()
        ..on(
          'POST /auth/forgot-password',
          (_) => errorResponse(
            429,
            'too_many_requests',
            'Too many requests. Please wait.',
          ),
        );
      await pumpApp(tester, server);
      await tester.tap(find.text('Forgot password?'));
      await tester.pumpAndSettle();
      await tester.enterText(sheetField(), 'jo@example.co.uk');
      await tester.tap(find.widgetWithText(FilledButton, 'Send me the link'));
      await tester.pumpAndSettle();
      expect(find.text('Too many requests. Please wait.'), findsOneWidget);
      expect(find.text('Reset your password'), findsOneWidget);
    });
  });

  group('opening the app', () {
    testWidgets('goes straight in when a sign-in was saved', (tester) async {
      final h = await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      expect(h.auth.status, AuthStatus.signedIn);
      expect(find.text('Your businesses'), findsOneWidget);
    });

    testWidgets(
      'says so when the server cannot be reached and tries again on request',
      (tester) async {
        final server = happyServer()..down = true;
        final h = await pumpApp(
          tester,
          server,
          store: MemorySessionStore()..refreshToken = 'saved',
        );
        expect(find.textContaining('Could not reach Vyterlix'), findsOneWidget);
        server.down = false;
        await tester.tap(find.text('Try again'));
        await tester.pumpAndSettle();
        expect(h.auth.status, AuthStatus.signedIn);
        expect(find.text('Your businesses'), findsOneWidget);
      },
    );
  });

  group('the list of businesses', () {
    testWidgets('lists them with the person\'s role in each', (tester) async {
      await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      expect(find.text('Fakeham Bakery'), findsOneWidget);
      expect(find.text('Second Shop'), findsOneWidget);
      expect(find.text('owner'), findsOneWidget);
      expect(find.text('viewer'), findsOneWidget);
    });

    testWidgets('opening one shows its Today screen and remembers the choice', (
      tester,
    ) async {
      final h = await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await tester.tap(find.text('Fakeham Bakery'));
      await tester.pumpAndSettle();
      expect(find.text('TODAY'), findsOneWidget);
      expect(h.memory.lastBusiness, 'b1');
    });

    testWidgets(
      'the business open last time opens by itself, and switching goes back to the list',
      (tester) async {
        final store = MemorySessionStore()
          ..refreshToken = 'saved'
          ..lastBusiness = 'b1';
        final h = await pumpApp(tester, happyServer(), store: store);
        expect(find.text('TODAY'), findsOneWidget);
        await tester.tap(find.byTooltip('Switch business'));
        await tester.pumpAndSettle();
        expect(find.text('Your businesses'), findsOneWidget);
        expect(h.memory.lastBusiness, isNull);
      },
    );

    testWidgets('a remembered business that is no longer there is ignored', (
      tester,
    ) async {
      final store = MemorySessionStore()
        ..refreshToken = 'saved'
        ..lastBusiness = 'gone';
      await pumpApp(tester, happyServer(), store: store);
      expect(find.text('Your businesses'), findsOneWidget);
      expect(find.text('TODAY'), findsNothing);
    });

    testWidgets('someone with no business is told what to do', (tester) async {
      final server = happyServer()..json('GET /organizations', []);
      await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      expect(
        find.textContaining("You don't belong to any business yet"),
        findsOneWidget,
      );
    });

    testWidgets('a problem loading them can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations', businessList);
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('Fakeham Bakery'), findsOneWidget);
    });

    testWidgets('logging out from here returns to the log in screen', (
      tester,
    ) async {
      final h = await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      expect(find.text('jo@example.co.uk'), findsOneWidget);
      await tester.tap(find.text('Log out'));
      await tester.pumpAndSettle();
      expect(find.text('Welcome back'), findsOneWidget);
      expect(h.memory.refreshToken, isNull);
    });
  });

  group('the Today screen', () {
    Future<Harness> openToday(WidgetTester tester, FakeServer server) async {
      final h = await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await tester.tap(find.text('Fakeham Bakery'));
      await tester.pumpAndSettle();
      return h;
    }

    testWidgets('opens with the headline and how healthy the business is', (
      tester,
    ) async {
      await openToday(tester, happyServer());
      expect(
        find.text('2 things need your attention today, 1 of them serious.'),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('health-score')), findsOneWidget);
      expect(find.text('69'), findsOneWidget);
      expect(find.text('Fair'), findsOneWidget);
      expect(
        find.text('The area pulling your health down most is Sales.'),
        findsOneWidget,
      );
      expect(
        find.text('Down from 89 the month before · September 2026'),
        findsOneWidget,
      );
    });

    testWidgets(
      'lists what needs attention, most serious first, with how serious',
      (tester) async {
        await openToday(tester, happyServer());
        expect(find.text('Needs your attention'), findsOneWidget);
        expect(find.text('Item 0 title'), findsOneWidget);
        expect(find.text('Item 1 detail'), findsOneWidget);
        expect(find.text('High'), findsOneWidget);
        expect(find.text('Medium'), findsOneWidget);
        expect(find.text('Alert'), findsOneWidget);
        expect(find.text('Late'), findsOneWidget);
        expect(
          tester.getTopLeft(find.text('Item 0 title')).dy,
          lessThan(tester.getTopLeft(find.text('Item 1 title')).dy),
        );
        await tester.scrollUntilVisible(
          find.textContaining('3 more.'),
          200,
          scrollable: find.byType(Scrollable).first,
        );
        expect(find.textContaining('3 more.'), findsOneWidget);
      },
    );

    testWidgets(
      'shows the key figures in pounds and per cent with good and bad news',
      (tester) async {
        await openToday(tester, happyServer());
        await tester.scrollUntilVisible(
          find.text('Gross margin'),
          300,
          scrollable: find.byType(Scrollable).first,
        );
        expect(find.text('£8,717.10'), findsOneWidget);
        expect(find.text('£5,439.00'), findsOneWidget);
        expect(find.text('75.6%'), findsOneWidget);
        expect(find.text('▲ 9.1% on the month before'), findsOneWidget);
        expect(find.text('▲ 8.2% on the month before'), findsOneWidget);
        final good = tester.widget<Text>(
          find.text('▲ 9.1% on the month before'),
        );
        final bad = tester.widget<Text>(
          find.text('▲ 8.2% on the month before'),
        );
        expect(good.style!.color, isNot(equals(bad.style!.color)));
        expect(find.text('September 2026'), findsOneWidget);
      },
    );

    testWidgets('a quiet business says nothing needs it', (tester) async {
      await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await tester.tap(find.text('Second Shop'));
      await tester.pumpAndSettle();
      expect(find.text('Nothing needs your attention today.'), findsOneWidget);
      expect(find.text('Nothing needs you right now.'), findsOneWidget);
      expect(find.byKey(const ValueKey('health-score')), findsNothing);
    });

    testWidgets('a business still being set up shows how far it has got', (
      tester,
    ) async {
      final server = happyServer()
        ..json('GET /organizations/b1/dashboard', dashboard(setup: true));
      await openToday(tester, server);
      expect(find.text('Finish setting up'), findsOneWidget);
      expect(find.textContaining('3 of 7 steps are done'), findsOneWidget);
    });

    testWidgets('a problem loading it can be tried again', (tester) async {
      final server = happyServer()
        ..on(
          'GET /organizations/b1/dashboard',
          (_) => errorResponse(
            403,
            'organization_suspended',
            'This organisation is suspended',
          ),
        );
      await openToday(tester, server);
      expect(find.text('This organisation is suspended'), findsOneWidget);
      server.json('GET /organizations/b1/dashboard', dashboard());
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('TODAY'), findsOneWidget);
    });

    testWidgets('pulling down refreshes it', (tester) async {
      final server = happyServer();
      await openToday(tester, server);
      expect(server.to('GET /organizations/b1/dashboard'), hasLength(1));
      await tester.fling(find.byType(ListView), const Offset(0, 400), 1000);
      await tester.pumpAndSettle();
      expect(server.to('GET /organizations/b1/dashboard'), hasLength(2));
    });

    testWidgets('a renewed sign-in is used without the person noticing', (
      tester,
    ) async {
      final server = happyServer();
      var spent = true;
      server.on('GET /organizations/b1/dashboard', (request) {
        if (spent) {
          spent = false;
          return errorResponse(
            401,
            'token_expired',
            'Your access token has expired.',
          );
        }
        return jsonResponse(dashboard());
      });
      await openToday(tester, server);
      expect(find.text('TODAY'), findsOneWidget);
      expect(
        server.to('POST /auth/refresh'),
        hasLength(2),
      ); // once at opening, once for this
    });

    testWidgets('logging out here goes all the way back to the log in screen', (
      tester,
    ) async {
      final h = await openToday(tester, happyServer());
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Log out'));
      await tester.pumpAndSettle();
      expect(find.text('Welcome back'), findsOneWidget);
      expect(find.text('TODAY'), findsNothing);
      expect(h.memory.refreshToken, isNull);
    });

    testWidgets(
      'the session ending while looking at it sends the person to log in',
      (tester) async {
        final server = happyServer();
        final h = await openToday(tester, server);
        server.on(
          'GET /organizations/b1/dashboard',
          (_) => errorResponse(
            401,
            'session_ended',
            'Your session has ended. Please log in again.',
          ),
        );
        server.on(
          'POST /auth/refresh',
          (_) => errorResponse(
            401,
            'session_ended',
            'Your session has ended. Please log in again.',
          ),
        );
        await tester.fling(find.byType(ListView), const Offset(0, 400), 1000);
        await tester.pumpAndSettle();
        expect(h.auth.status, AuthStatus.signedOut);
        expect(find.text('Welcome back'), findsOneWidget);
      },
    );
  });

  testWidgets('http is only ever spoken to the configured server', (
    tester,
  ) async {
    final server = happyServer();
    await pumpApp(
      tester,
      server,
      store: MemorySessionStore()..refreshToken = 'saved',
    );
    await tester.tap(find.text('Fakeham Bakery'));
    await tester.pumpAndSettle();
    for (final http.Request request in server.requests) {
      expect(request.url.host, 'test.local');
    }
  });
}
