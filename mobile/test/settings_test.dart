import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/auth/session_store.dart';

import 'support.dart';

Map<String, dynamic> settingsJson({bool quiet = true}) => {
  'timezone': 'Europe/London',
  'locale': 'en-GB',
  'currency': 'GBP',
  'date_format': 'dd/mm/yyyy',
  'week_start_day': 1,
  'week_start_name': 'Monday',
  'quiet_hours': quiet ? {'start': '22:00:00', 'end': '07:00:00'} : null,
};

Map<String, dynamic> member(
  String id,
  String name,
  String email,
  String role,
) => {
  'user_id': id,
  'email': email,
  'full_name': name,
  'role': role,
  'remit': null,
  'status': 'active',
  'joined_at': '2026-03-01T10:00:00',
};

FakeServer settingsServer({bool quiet = true}) => happyServer()
  ..json('GET /organizations/b1/settings', settingsJson(quiet: quiet))
  ..json('PATCH /organizations/b1/settings', settingsJson())
  ..json('GET /organizations/b1/notification-preferences', [
    {
      'category': 'sales',
      'email': true,
      'in_app': true,
      'push': false,
      'locked': false,
    },
    {
      'category': 'security',
      'email': true,
      'in_app': true,
      'push': true,
      'locked': true,
    },
  ])
  ..json('PATCH /organizations/b1/notification-preferences', [])
  ..json('GET /organizations/b1/members', [
    member('u1', 'Jo Baker', 'jo@example.co.uk', 'owner'),
    member('u2', 'Sam Lee', 'sam@example.co.uk', 'viewer'),
  ])
  ..json(
    'PATCH /organizations/b1/members/u2',
    member('u2', 'Sam Lee', 'sam@example.co.uk', 'manager'),
  )
  ..json('GET /organizations/b1/invitations', [
    {
      'id': 'i1',
      'email': 'new@example.co.uk',
      'role': 'manager',
      'status': 'pending',
    },
    {
      'id': 'i2',
      'email': 'old@example.co.uk',
      'role': 'viewer',
      'status': 'expired',
    },
  ])
  ..json('POST /organizations/b1/invitations', {
    'id': 'i3',
    'email': 'a@b.co.uk',
    'role': 'viewer',
    'status': 'pending',
  }, 201)
  ..on(
    'DELETE /organizations/b1/invitations/i1',
    (_) => http.Response('', 204),
  );

Future<void> open(
  WidgetTester tester,
  FakeServer server, {
  String shop = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 5000);
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
  await tester.tap(find.text('Settings'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows the settings, my notifications and the team', (
    tester,
  ) async {
    await open(tester, settingsServer());
    expect(
      find.text(
        'Times are UK time (Europe/London); dates show as dd/mm/yyyy; money in GBP.',
      ),
      findsOneWidget,
    );
    expect(find.text('From 22:00'), findsOneWidget);
    expect(find.text('Until 07:00'), findsOneWidget);
    expect(find.text('Money and cash flow'), findsNothing);
    expect(find.text('Sales'), findsOneWidget);
    expect(find.text('Jo Baker'), findsOneWidget);
    expect(find.text('jo@example.co.uk · joined 01/03/2026'), findsOneWidget);
    expect(find.text('new@example.co.uk'), findsOneWidget);
    expect(find.text('old@example.co.uk'), findsNothing);
  });

  testWidgets('the owner can change the week start and quiet hours', (
    tester,
  ) async {
    final server = settingsServer();
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('week-start')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sunday').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(server.to('PATCH /organizations/b1/settings').single),
      {
        'week_start_day': 7,
        'quiet_hours': {'start': '22:00', 'end': '07:00'},
      },
    );
    expect(find.text('Settings saved.'), findsOneWidget);
  });

  testWidgets('switching quiet hours off sends null', (tester) async {
    final server = settingsServer();
    await open(tester, server);
    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.text('From 22:00'), findsNothing);
    await tester.tap(find.text('Save settings'));
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(
        server.to('PATCH /organizations/b1/settings').single,
      )['quiet_hours'],
      isNull,
    );
  });

  testWidgets(
    'my notification choices are sent, only what changed; security cannot be switched off',
    (tester) async {
      final server = settingsServer();
      await open(tester, server);
      expect(
        tester
            .widget<FilterChip>(
              find.byKey(const ValueKey('pref-security-email')),
            )
            .onSelected,
        isNull,
      );
      expect(
        tester
            .widget<FilterChip>(
              find.byKey(const ValueKey('pref-security-push')),
            )
            .onSelected,
        isNotNull,
      );
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Save my notifications'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const ValueKey('pref-sales-email')));
      await tester.tap(find.byKey(const ValueKey('pref-sales-push')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save my notifications'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('PATCH /organizations/b1/notification-preferences').single,
        ),
        {
          'preferences': {
            'sales': {'email': false, 'push': true},
          },
        },
      );
      expect(find.text('Your notification choices are saved.'), findsOneWidget);
    },
  );

  testWidgets('the owner can change someone\'s role, but not their own', (
    tester,
  ) async {
    final server = settingsServer();
    await open(tester, server);
    expect(find.byKey(const ValueKey('role-u1')), findsNothing);
    await tester.tap(find.byKey(const ValueKey('role-u2')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Manager').last);
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(server.to('PATCH /organizations/b1/members/u2').single),
      {'role': 'manager'},
    );
    expect(find.text('Sam Lee is now manager.'), findsOneWidget);
  });

  testWidgets('invites someone, and checks the address first', (tester) async {
    final server = settingsServer();
    await open(tester, server);
    await tester.tap(find.text('Send invitation'));
    await tester.pumpAndSettle();
    expect(find.text('Type the email address of the person.'), findsOneWidget);
    expect(server.to('POST /organizations/b1/invitations'), isEmpty);
    await tester.enterText(
      find.widgetWithText(TextField, 'Email'),
      ' a@b.co.uk ',
    );
    await tester.tap(find.text('Send invitation'));
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(server.to('POST /organizations/b1/invitations').single),
      {'email': 'a@b.co.uk', 'role': 'viewer'},
    );
    expect(find.text('Invitation sent to a@b.co.uk.'), findsOneWidget);
  });

  testWidgets('a pending invitation can be cancelled, after asking', (
    tester,
  ) async {
    final server = settingsServer();
    await open(tester, server);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Keep it'));
    await tester.pumpAndSettle();
    expect(server.to('DELETE /organizations/b1/invitations/i1'), isEmpty);
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel it'));
    await tester.pumpAndSettle();
    expect(server.to('DELETE /organizations/b1/invitations/i1'), hasLength(1));
    expect(find.text('Invitation cancelled.'), findsOneWidget);
  });

  testWidgets('a refusal is shown', (tester) async {
    final server = settingsServer()
      ..on(
        'POST /organizations/b1/invitations',
        (_) => errorResponse(
          409,
          'already_member',
          'That person is already in your team.',
        ),
      );
    await open(tester, server);
    await tester.enterText(
      find.widgetWithText(TextField, 'Email'),
      'sam@example.co.uk',
    );
    await tester.tap(find.text('Send invitation'));
    await tester.pumpAndSettle();
    expect(find.text('That person is already in your team.'), findsOneWidget);
  });

  testWidgets(
    'a viewer can see but not change anything, and only manages their own notifications',
    (tester) async {
      final server = settingsServer();
      for (final k in server.routes.keys.toList()) {
        if (k.contains('/b1/')) {
          server.routes[k.replaceFirst('/b1/', '/b2/')] = server.routes[k]!;
        }
      }
      await open(tester, server, shop: 'Second Shop');
      expect(find.text('Only an owner can change these.'), findsOneWidget);
      expect(find.text('Save settings'), findsNothing);
      expect(find.text('Send invitation'), findsNothing);
      expect(find.text('Save my notifications'), findsOneWidget);
      expect(server.to('GET /organizations/b2/invitations'), isEmpty);
    },
  );

  testWidgets('a problem loading can be tried again', (tester) async {
    final server = settingsServer()
      ..on(
        'GET /organizations/b1/settings',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
    await open(tester, server);
    expect(find.text('An unexpected error occurred'), findsOneWidget);
    server.json('GET /organizations/b1/settings', settingsJson());
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('From 22:00'), findsOneWidget);
  });
}
