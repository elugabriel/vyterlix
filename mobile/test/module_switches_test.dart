import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'settings_test.dart' show open, settingsServer;
import 'support.dart';

Map<String, dynamic> part(
  String code,
  String name,
  String status, {
  String? reason,
  bool? canSwitch,
}) => {
  'code': code,
  'name': name,
  'summary': 'About $name.',
  'enabled': status != 'switched_off',
  'status': status,
  'reason': reason,
  'can_switch': canSwitch ?? (status == 'shown' || status == 'switched_off'),
};

FakeServer withParts() => settingsServer()
  ..json('GET /organizations/b1/modules', [
    part('sales', 'Sales & Revenue', 'shown'),
    part('bookings', 'Bookings & Appointments', 'shown'),
    part(
      'inventory',
      'Stock & Inventory',
      'not_for_you',
      reason: 'This is for businesses that sell products.',
    ),
    part(
      'suppliers',
      'Supplier Performance',
      'no_figures',
      reason: 'The figures for this are still being built.',
    ),
  ])
  ..json('PUT /organizations/b1/modules/bookings', {
    ...part('bookings', 'Bookings & Appointments', 'switched_off'),
  });

void main() {
  testWidgets('the settings list the parts of Vyterlix and which are on', (
    tester,
  ) async {
    await open(tester, withParts());
    expect(find.text('What Vyterlix shows you'), findsOneWidget);
    expect(find.text('Sales & Revenue'), findsOneWidget);
    expect(find.text('Bookings & Appointments'), findsOneWidget);
    expect(
      find.textContaining('This is for businesses that sell products.'),
      findsOneWidget,
    );
    expect(
      find.textContaining('The figures for this are still being built.'),
      findsOneWidget,
    );
    final bookings = tester.widget<SwitchListTile>(
      find.byKey(const ValueKey('part-bookings')),
    );
    expect(bookings.value, isTrue);
    expect(bookings.onChanged, isNotNull);
  });

  testWidgets(
    'a part that is not for this business or has nothing yet cannot be switched',
    (tester) async {
      await open(tester, withParts());
      for (final code in ['inventory', 'suppliers']) {
        final tile = tester.widget<SwitchListTile>(
          find.byKey(ValueKey('part-$code')),
        );
        expect(tile.onChanged, isNull, reason: code);
        expect(tile.value, isFalse, reason: code);
      }
    },
  );

  testWidgets('switching a part off tells the server and says so', (
    tester,
  ) async {
    final server = withParts();
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('part-bookings')));
    await tester.pumpAndSettle();
    final body = server.bodyOf(
      server.to('PUT /organizations/b1/modules/bookings').single,
    );
    expect(body['enabled'], false);
    expect(
      find.textContaining('Bookings & Appointments is now hidden.'),
      findsOneWidget,
    );
  });

  testWidgets('the settings still work when the list of parts cannot be had', (
    tester,
  ) async {
    await open(tester, settingsServer());
    expect(find.text('What Vyterlix shows you'), findsNothing);
    expect(find.text('Business settings'), findsOneWidget);
  });
}
