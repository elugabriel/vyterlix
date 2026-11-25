import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/screens/welcome_screen.dart';

void main() {
  testWidgets('the welcome screen says what Vyterlix is and Log in moves on', (
    tester,
  ) async {
    var tapped = 0;
    await tester.pumpWidget(
      MaterialApp(home: WelcomeScreen(onContinue: () => tapped++)),
    );

    expect(
      find.textContaining('Know how your business is really doing'),
      findsOneWidget,
    );
    expect(find.text('See where you stand'), findsOneWidget);
    expect(find.text('Get warned early'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, 'Log in'));
    expect(tapped, 1);
  });

  testWidgets('it fits a small phone without overflowing', (tester) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      MaterialApp(home: WelcomeScreen(onContinue: () {})),
    );
    expect(tester.takeException(), isNull);
  });
}
