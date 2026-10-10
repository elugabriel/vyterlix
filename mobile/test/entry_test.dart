import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';

import 'data_test.dart' show dataServer, FakeFiles;
import 'support.dart';

FakeServer entryServer() => dataServer()
  ..json('GET /organizations/b1/lists/sales_channel', [
    {'id': 'c1', 'name': 'Shop counter', 'is_active': true},
    {'id': 'c2', 'name': 'Old channel', 'is_active': false},
  ])
  ..json('GET /organizations/b1/lists/cost_category', [
    {'id': 'k1', 'name': 'Rent', 'is_active': true},
  ])
  ..json('POST /organizations/b1/sales', {
    'id': 's1',
    'gross_amount': '12.50',
  }, 201)
  ..json('POST /organizations/b1/expenses', {
    'id': 'e1',
    'gross_amount': '240.00',
  }, 201);

Future<void> open(
  WidgetTester tester,
  FakeServer server, {
  String shop = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 3200);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
    files: FakeFiles(),
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
  await tester.tap(find.text('Your data'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('only the owner is offered typing in data', (tester) async {
    await open(tester, entryServer(), shop: 'Second Shop');
    expect(find.text('Type in a sale or expense'), findsNothing);
  });

  testWidgets('a sale is sent with the VAT answers and shows the total', (
    tester,
  ) async {
    final server = entryServer();
    await open(tester, server);
    await tester.tap(find.text('Type in a sale or expense'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextField, 'Amount (£)'),
      '12.50',
    );
    await tester.enterText(
      find.widgetWithText(TextField, 'Reference (optional)'),
      ' INV-1 ',
    );
    await tester.tap(find.text('No, it\'s before VAT'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save sale'));
    await tester.pumpAndSettle();
    final body = server.bodyOf(
      server.to('POST /organizations/b1/sales').single,
    );
    expect(body['amount'], '12.50');
    expect(body['amount_includes_vat'], false);
    expect(body['vat_rate'], '20');
    expect(body['kind'], 'sale');
    expect(body['reference'], 'INV-1');
    expect(body['lines'], isEmpty);
    expect(body.containsKey('vat_amount'), isFalse);
    expect(find.text('Sale saved: £12.50 in total.'), findsOneWidget);
    expect(
      tester
          .widget<TextField>(find.widgetWithText(TextField, 'Amount (£)'))
          .controller!
          .text,
      '',
    );
  });

  testWidgets('a refund with a typed VAT amount and a channel', (tester) async {
    final server = entryServer();
    await open(tester, server);
    await tester.tap(find.text('Type in a sale or expense'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Switch));
    await tester.enterText(find.widgetWithText(TextField, 'Amount (£)'), '30');
    await tester.tap(find.byKey(const ValueKey('entry-vat')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('I\'ll type the VAT amount').last);
    await tester.pumpAndSettle();
    await tester.enterText(
      find.widgetWithText(TextField, 'VAT amount (£)'),
      '5',
    );
    await tester.tap(find.byKey(const ValueKey('entry-pick-true')));
    await tester.pumpAndSettle();
    expect(find.text('Old channel'), findsNothing);
    await tester.tap(find.text('Shop counter').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save sale'));
    await tester.pumpAndSettle();
    final body = server.bodyOf(
      server.to('POST /organizations/b1/sales').single,
    );
    expect(body['kind'], 'refund');
    expect(body['vat_amount'], '5');
    expect(body.containsKey('vat_rate'), isFalse);
    expect(body['sales_channel_id'], 'c1');
  });

  testWidgets('an expense goes to expenses with its category and reason', (
    tester,
  ) async {
    final server = entryServer();
    await open(tester, server);
    await tester.tap(find.text('Type in a sale or expense'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Expense'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Amount (£)'), '240');
    await tester.enterText(
      find.widgetWithText(TextField, 'What it was for (optional)'),
      'October rent',
    );
    await tester.tap(find.byKey(const ValueKey('entry-pick-false')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Rent').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Save expense'));
    await tester.pumpAndSettle();
    final body = server.bodyOf(
      server.to('POST /organizations/b1/expenses').single,
    );
    expect(body['kind'], 'expense');
    expect(body['description'], 'October rent');
    expect(body['cost_category_id'], 'k1');
    expect(body['spent_on'], isNotNull);
    expect(server.to('POST /organizations/b1/sales'), isEmpty);
    expect(find.text('Expense saved: £240.00 in total.'), findsOneWidget);
  });

  testWidgets(
    'a missing amount is caught before sending, and a refusal is shown',
    (tester) async {
      final server = entryServer()
        ..on(
          'POST /organizations/b1/sales',
          (_) => errorResponse(
            409,
            'duplicate_reference',
            'That reference has been used already.',
          ),
        );
      await open(tester, server);
      await tester.tap(find.text('Type in a sale or expense'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save sale'));
      await tester.pumpAndSettle();
      expect(
        find.text('Type the amount in pounds, for example 12.50.'),
        findsOneWidget,
      );
      expect(server.to('POST /organizations/b1/sales'), isEmpty);
      await tester.enterText(find.widgetWithText(TextField, 'Amount (£)'), '5');
      await tester.tap(find.text('Save sale'));
      await tester.pumpAndSettle();
      expect(
        find.text('That reference has been used already.'),
        findsOneWidget,
      );
    },
  );
}
