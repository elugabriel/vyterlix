import 'package:flutter_test/flutter_test.dart';

import 'insights_test.dart' show kpisJson, openBusiness, openMore;
import 'support.dart';

/// The figures as the server sends them once they belong to the parts of Vyterlix a business sees
/// (client feedback 15/11/2026): each figure names its module, and the modules come in order.
Map<String, dynamic> withModules() {
  final body = kpisJson();
  body['modules'] = [
    {
      'code': 'sales',
      'name': 'Sales & Revenue',
      'summary': 'What you sold and how it is moving.',
    },
    {
      'code': 'financial',
      'name': 'Financial Intelligence',
      'summary': 'Profit, running costs, money owed and money in the bank.',
    },
  ];
  for (final k in body['kpis'] as List) {
    final figure = k as Map<String, dynamic>;
    figure['module'] = figure['category'] == 'sales' ? 'sales' : 'financial';
  }
  return body;
}

void main() {
  group('Key figures by part of Vyterlix', () {
    testWidgets(
      'are grouped under the modules the server names, in its order',
      (tester) async {
        final server = happyServer()
          ..json('GET /organizations/b1/kpis', withModules());
        await openBusiness(tester, server);
        await openMore(tester, 'Key figures');
        expect(find.text('Sales & Revenue'), findsOneWidget);
        expect(find.text('Financial Intelligence'), findsOneWidget);
        expect(
          find.text('What you sold and how it is moving.'),
          findsOneWidget,
        );
        expect(
          tester.getTopLeft(find.text('Sales & Revenue')).dy,
          lessThan(tester.getTopLeft(find.text('Financial Intelligence')).dy),
        );
        // the old five plain groups are not shown as well
        expect(find.text('Financial Health'), findsNothing);
      },
    );

    testWidgets(
      'fall back to the plain groups when the server names no modules',
      (tester) async {
        await openBusiness(tester, happyServer());
        await openMore(tester, 'Key figures');
        expect(find.text('Financial Health'), findsWidgets);
        expect(find.text('Sales & Revenue'), findsNothing);
      },
    );
  });
}
