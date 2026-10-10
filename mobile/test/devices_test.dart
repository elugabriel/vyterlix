import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/auth/session_store.dart';

import 'support.dart';

List<Map<String, dynamic>> sessionsJson() => [
  {
    'id': 's1',
    'client': 'mobile',
    'device_name': 'Pixel 8',
    'user_agent': null,
    'created_at': '2026-10-01T08:00:00',
    'last_used_at': '2026-10-09T13:05:00',
    'expires_at': '2026-11-01T08:00:00',
    'current': true,
  },
  {
    'id': 's2',
    'client': 'web',
    'device_name': null,
    'user_agent': 'Firefox',
    'created_at': '2026-09-20T08:00:00',
    'last_used_at': null,
    'expires_at': '2026-11-01T08:00:00',
    'current': false,
  },
];

Future<void> open(WidgetTester tester, FakeServer server) async {
  tester.view.physicalSize = const Size(800, 3000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
  );
  await tester.tap(find.byTooltip('Account'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Where you are signed in'));
  await tester.pumpAndSettle();
}

FakeServer devicesServer() => happyServer()
  ..json('GET /me/sessions', sessionsJson())
  ..on('DELETE /me/sessions/s2', (_) => http.Response('', 204))
  ..json('POST /me/sessions/revoke-others', {'sessions_ended': 1});

void main() {
  testWidgets('lists every device, this one marked and not endable', (
    tester,
  ) async {
    await open(tester, devicesServer());
    expect(find.text('Pixel 8'), findsOneWidget);
    expect(find.text('This device'), findsOneWidget);
    expect(find.text('A web browser'), findsOneWidget);
    expect(find.text('Last used 09/10/2026, 13:05'), findsOneWidget);
    expect(find.text('Signed in 20/09/2026, 08:00'), findsOneWidget);
    expect(find.byKey(const ValueKey('end-s1')), findsNothing);
    expect(find.byKey(const ValueKey('end-s2')), findsOneWidget);
  });

  testWidgets('another device can be signed out, after asking', (tester) async {
    final server = devicesServer();
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('end-s2')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Keep it'));
    await tester.pumpAndSettle();
    expect(server.to('DELETE /me/sessions/s2'), isEmpty);
    await tester.tap(find.byKey(const ValueKey('end-s2')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sign it out').last);
    await tester.pumpAndSettle();
    expect(server.to('DELETE /me/sessions/s2'), hasLength(1));
    expect(find.text('Signed out of A web browser.'), findsOneWidget);
  });

  testWidgets('every other device can be signed out at once', (tester) async {
    final server = devicesServer();
    await open(tester, server);
    await tester.tap(find.text('Sign out of every other device'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Sign them out'));
    await tester.pumpAndSettle();
    expect(server.to('POST /me/sessions/revoke-others'), hasLength(1));
    expect(
      find.text('Every other device has been signed out.'),
      findsOneWidget,
    );
  });

  testWidgets('a refusal is shown, and a failed load can be tried again', (
    tester,
  ) async {
    final server = devicesServer()
      ..on(
        'GET /me/sessions',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
    await open(tester, server);
    expect(find.text('An unexpected error occurred'), findsOneWidget);
    server.json('GET /me/sessions', sessionsJson());
    await tester.tap(find.text('Try again'));
    await tester.pumpAndSettle();
    expect(find.text('Pixel 8'), findsOneWidget);
  });
}
