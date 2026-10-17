import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/models.dart';

import 'insights_test.dart' show openBusiness, openMore;
import 'support.dart';

Map<String, dynamic> followUp() => {
  'as_of': '2026-09-28',
  'quiet_days': 60,
  'total': 3,
  'customers': [
    {
      'customer_id': 'c1',
      'name': 'Ben Ola',
      'last_bought_on': '2026-07-20',
      'days_since': 70,
      'orders': 1,
      'total_spent': '500.00',
    },
    {
      'customer_id': 'c2',
      'name': 'Ana Cole',
      'last_bought_on': '2026-06-20',
      'days_since': 100,
      'orders': 2,
      'total_spent': '300.00',
    },
  ],
};

FakeServer serverFor(String? sells) {
  final server = happyServer()
    ..json('GET /organizations', [
      {'id': 'b1', 'name': 'Fakeham Bakery', 'role': 'owner', 'sells': ?sells},
      {'id': 'b2', 'name': 'Second Shop', 'role': 'viewer'},
    ])
    ..json('GET /organizations/b1/kpis/customers/follow-up', followUp())
    ..json('GET /organizations/b1/kpis/services', {
      'rows': [
        {
          'service': 'Cut',
          'kept': 7,
          'cancelled': 2,
          'missed': 0,
          'rebooked_pct': '83.3',
        },
        {
          'service': 'Colour',
          'kept': 6,
          'cancelled': 0,
          'missed': 1,
          'rebooked_pct': '16.7',
        },
      ],
      'notes': [
        'Cut is your most booked service: 7 kept in the last three months.',
      ],
    });
  return server;
}

void main() {
  group('what the business sells', () {
    test('is read, and an older server that does not send it still works', () {
      expect(
        Business.fromJson({
          'id': 'b',
          'name': 'N',
          'role': 'owner',
          'sells': 'services',
        }).sells,
        'services',
      );
      final b = Business.fromJson({'id': 'b', 'name': 'N', 'role': 'owner'});
      expect(b.sells, isNull);
      expect(b.hasStock && b.showFollowUp, isTrue);
    });

    test(
      'a service has no stock and a product seller has no follow-up list',
      () {
        const services = Business(
          id: 'b',
          name: 'N',
          role: 'owner',
          sells: 'services',
        );
        const products = Business(
          id: 'b',
          name: 'N',
          role: 'owner',
          sells: 'products',
        );
        expect(services.hasStock, isFalse);
        expect(services.showFollowUp, isTrue);
        expect(products.hasStock, isTrue);
        expect(products.showFollowUp, isFalse);
      },
    );
  });

  group('on the phone', () {
    testWidgets('a service business is shown customers to follow up', (
      tester,
    ) async {
      await openBusiness(tester, serverFor('services'));
      await openMore(tester, 'Key figures');
      await tester.scrollUntilVisible(
        find.text('Customers to follow up'),
        400,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Customers to follow up'), findsOneWidget);
      expect(
        find.textContaining('3 customers have not bought'),
        findsOneWidget,
      );
      await tester.scrollUntilVisible(
        find.text('Ben Ola'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      expect(find.text('Ben Ola'), findsOneWidget);
      expect(find.text('£500.00'), findsOneWidget);
    });

    testWidgets(
      'a service business sees which services people book and return for',
      (tester) async {
        await openBusiness(tester, serverFor('services'));
        await openMore(tester, 'Key figures');
        await tester.scrollUntilVisible(
          find.text('Your services'),
          400,
          scrollable: find.byType(Scrollable).first,
        );
        expect(
          find.textContaining('Cut is your most booked service'),
          findsOneWidget,
        );
        expect(
          find.text('7 kept · 2 cancelled · 0 did not turn up'),
          findsOneWidget,
        );
        expect(find.text('16.7% book again'), findsOneWidget);
      },
    );

    testWidgets('a business that sells products is not shown that list', (
      tester,
    ) async {
      final server = serverFor('products');
      await openBusiness(tester, server);
      await openMore(tester, 'Key figures');
      expect(find.text('Customers to follow up'), findsNothing);
      expect(
        server.to('GET /organizations/b1/kpis/customers/follow-up'),
        isEmpty,
      );
    });

    testWidgets('a service business is not shown what to stock', (
      tester,
    ) async {
      final server = serverFor('services');
      await openBusiness(tester, server);
      await openMore(tester, 'Forecast');
      expect(find.text('What to stock'), findsNothing);
      expect(
        server.to('GET /organizations/b1/forecasts/stock-requirements'),
        isEmpty,
      );
    });

    testWidgets('everyone else still sees what to stock', (tester) async {
      await openBusiness(tester, serverFor(null));
      await openMore(tester, 'Forecast');
      expect(find.text('What to stock'), findsOneWidget);
    });
  });
}
