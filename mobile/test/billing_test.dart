import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/screens/billing_screen.dart';

import 'support.dart';

Map<String, dynamic> billingJson({
  String status = 'active',
  bool canManage = true,
  bool cancelling = false,
  String? scheduled,
}) => {
  'can_manage': canManage,
  'providers': ['sandbox'],
  'subscription': {
    'plan_code': 'grow',
    'plan_name': 'Grow',
    'status': status,
    'status_label': status == 'trialing' ? 'Free trial' : 'Active',
    'message': 'Your plan renews on 01/11/2026.',
    'interval': 'month',
    'scheduled_plan_name': scheduled,
    'cancel_at_period_end': cancelling,
    'current_period_start': '2026-10-01T00:00:00',
    'current_period_end': '2026-11-01T00:00:00',
  },
  'features': [
    {'label': 'People on your team', 'enabled': true, 'used': 2, 'limit': 5},
    {'label': 'Connections', 'enabled': true, 'used': 1, 'limit': null},
    {
      'label': 'Business questions',
      'enabled': true,
      'used': null,
      'limit': null,
    },
    {
      'label': 'Scheduled reports',
      'enabled': false,
      'used': null,
      'limit': null,
    },
  ],
};

List<Map<String, dynamic>> plansJson() => [
  {
    'code': 'grow',
    'name': 'Grow',
    'description': 'For growing shops.',
    'price_month': '£49.00',
    'price_year': '£490.00',
    'year_saving': '£98.00',
    'self_serve': true,
    'current': true,
    'vat_note': '',
    'features': [
      {'label': 'Team', 'text': 'Up to 5'},
    ],
  },
  {
    'code': 'scale',
    'name': 'Scale',
    'description': 'For busy shops.',
    'price_month': '£99.00',
    'price_year': '£990.00',
    'year_saving': '£198.00',
    'self_serve': true,
    'current': false,
    'vat_note': '',
    'features': [
      {'label': 'Team', 'text': 'Up to 20'},
    ],
  },
  {
    'code': 'enterprise',
    'name': 'Enterprise',
    'description': 'Large groups.',
    'price_month': null,
    'price_year': null,
    'year_saving': null,
    'self_serve': false,
    'current': false,
    'vat_note': 'Contact us for a quote.',
    'features': [],
  },
];

FakeServer billingServer({
  String status = 'active',
  bool canManage = true,
  bool cancelling = false,
}) => happyServer()
  ..json(
    'GET /organizations/b1/billing',
    billingJson(status: status, canManage: canManage, cancelling: cancelling),
  )
  ..json('GET /organizations/b1/billing/plans', plansJson())
  ..json('GET /organizations/b1/billing/invoices', [
    {
      'id': 'v1',
      'number': 'INV-0001',
      'issued_at': '2026-10-01T09:00:00',
      'net': '£49.00',
      'vat': '£9.80',
      'total': '£58.80',
      'status_label': 'Paid',
      'hosted_url': null,
      'lines': [
        {'description': 'Grow plan, October'},
      ],
    },
  ])
  ..json('POST /organizations/b1/billing/cancel', billingJson(cancelling: true))
  ..json('POST /organizations/b1/billing/resume', billingJson())
  ..json('POST /organizations/b1/billing/change', {
    'result': 'changed_now',
    'message': 'You are now on the Scale plan.',
    'checkout_url': null,
    'billing': billingJson(),
  });

Future<void> open(
  WidgetTester tester,
  FakeServer server, {
  String shop = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 6000);
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
  await tester.tap(find.text('Plan and billing'));
  await tester.pumpAndSettle();
}

void main() {
  test('usage is put in plain words', () {
    expect(usageText({'enabled': false}), 'Not included');
    expect(
      usageText({'enabled': true, 'used': null, 'limit': null}),
      'Included',
    );
    expect(
      usageText({'enabled': true, 'used': 2, 'limit': null}),
      '2 in use, no limit',
    );
    expect(
      usageText({'enabled': true, 'used': 2, 'limit': 5}),
      '2 of 5 in use',
    );
  });

  testWidgets('shows the plan, what is used, the plans and the invoices', (
    tester,
  ) async {
    await open(tester, billingServer());
    expect(find.text('Grow'), findsWidgets);
    expect(find.text('Your plan renews on 01/11/2026.'), findsOneWidget);
    expect(
      find.text('Current period: 01/10/2026 to 01/11/2026'),
      findsOneWidget,
    );
    expect(find.text('2 of 5 in use'), findsOneWidget);
    expect(find.text('1 in use, no limit'), findsOneWidget);
    expect(find.text('Not included'), findsOneWidget);
    expect(find.text('£49.00 a month'), findsOneWidget);
    expect(find.text('Talk to us'), findsOneWidget);
    expect(find.text('Contact us for a quote.'), findsOneWidget);
    expect(find.text('INV-0001 · £58.80'), findsOneWidget);
    expect(
      find.textContaining('Net £49.00 · VAT £9.80 · Paid'),
      findsOneWidget,
    );
  });

  testWidgets('yearly prices and savings can be shown', (tester) async {
    await open(tester, billingServer());
    await tester.tap(find.text('Pay yearly'));
    await tester.pumpAndSettle();
    expect(find.text('£490.00 a year'), findsOneWidget);
    expect(find.text('Saves £98.00 a year'), findsOneWidget);
  });

  testWidgets('moving to another plan asks the server and says what happened', (
    tester,
  ) async {
    final server = billingServer();
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('move-scale')));
    await tester.pumpAndSettle();
    expect(
      server.bodyOf(server.to('POST /organizations/b1/billing/change').single),
      {'plan_code': 'scale', 'interval': 'month'},
    );
    expect(find.text('You are now on the Scale plan.'), findsOneWidget);
  });

  testWidgets('a change that needs paying is sent to the website', (
    tester,
  ) async {
    final server = billingServer()
      ..json('POST /organizations/b1/billing/change', {
        'result': 'checkout',
        'message': 'x',
        'checkout_url': 'https://pay.example',
        'billing': billingJson(),
      });
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('move-scale')));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'To finish this change, open Vyterlix on the website and pay there.',
      ),
      findsOneWidget,
    );
  });

  testWidgets(
    'cancelling asks first, then cancels; a cancelling plan can be kept',
    (tester) async {
      final server = billingServer();
      await open(tester, server);
      await tester.tap(find.text('Cancel my plan'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Keep it'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/billing/cancel'), isEmpty);
      await tester.tap(find.text('Cancel my plan'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel it'));
      await tester.pumpAndSettle();
      expect(server.to('POST /organizations/b1/billing/cancel'), hasLength(1));
      expect(
        find.text('Your plan will end when the period you have paid for does.'),
        findsOneWidget,
      );
    },
  );

  testWidgets('a plan that is ending can be kept', (tester) async {
    final other = billingServer(cancelling: true);
    await open(tester, other);
    await tester.tap(find.text('Keep my plan'));
    await tester.pumpAndSettle();
    expect(other.to('POST /organizations/b1/billing/resume'), hasLength(1));
  });

  testWidgets('on a trial, choosing a plan points to the website', (
    tester,
  ) async {
    await open(tester, billingServer(status: 'trialing'));
    expect(find.text('Free trial'), findsOneWidget);
    expect(
      find.text('To choose Scale, open Vyterlix on the website and pay there.'),
      findsOneWidget,
    );
    expect(find.text('Cancel my plan'), findsNothing);
  });

  testWidgets(
    'someone who is not the owner can look but not change, and cannot see invoices',
    (tester) async {
      final server = billingServer(canManage: false)
        ..on(
          'GET /organizations/b1/billing/invoices',
          (_) => errorResponse(403, 'forbidden', 'Not allowed.'),
        );
      await open(tester, server);
      expect(find.text('Cancel my plan'), findsNothing);
      expect(find.byKey(const ValueKey('move-scale')), findsNothing);
      expect(
        find.text('Only the owner can see what has been charged.'),
        findsOneWidget,
      );
    },
  );

  testWidgets('a refusal is shown', (tester) async {
    final server = billingServer()
      ..on(
        'POST /organizations/b1/billing/change',
        (_) => errorResponse(409, 'not_allowed', 'That plan is not available.'),
      );
    await open(tester, server);
    await tester.tap(find.byKey(const ValueKey('move-scale')));
    await tester.pumpAndSettle();
    expect(find.text('That plan is not available.'), findsOneWidget);
  });

  testWidgets('a failed load can be tried again', (tester) async {
    final broken = billingServer()
      ..on(
        'GET /organizations/b1/billing',
        (_) => errorResponse(
          500,
          'internal_error',
          'An unexpected error occurred',
        ),
      );
    await open(tester, broken);
    expect(find.text('An unexpected error occurred'), findsOneWidget);
  });
}
