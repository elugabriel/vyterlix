import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/lock.dart';

import 'support.dart';

MemorySessionStore signedInStore({bool lock = false}) => MemorySessionStore()
  ..refreshToken = 'saved'
  ..lockEnabled = lock;

Future<void> background(WidgetTester tester) async {
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
  await tester.pump();
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
  tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
  await tester.pumpAndSettle();
}

void main() {
  group('the lock itself', () {
    test(
      'is off unless the person turned it on and the phone can check',
      () async {
        final store = signedInStore(lock: true);
        final off = AppLock(
          phone: FakePhoneLock(supported: false),
          store: store,
        );
        await off.load();
        expect((off.enabled, off.locked), (false, false));
        final on = AppLock(phone: FakePhoneLock(), store: store);
        await on.load();
        expect((on.enabled, on.locked), (true, true)); // starts locked
      },
    );

    test('turning on needs the phone to check the person first', () async {
      final store = signedInStore();
      final phone = FakePhoneLock(passes: false);
      final lock = AppLock(phone: phone, store: store);
      await lock.load();
      expect(await lock.enable(), isFalse);
      expect(store.lockEnabled, isFalse);
      phone.passes = true;
      expect(await lock.enable(), isTrue);
      expect(store.lockEnabled, isTrue);
      await lock.disable();
      expect((lock.enabled, store.lockEnabled), (false, false));
    });

    test('locks only after being away long enough', () async {
      final lock = AppLock(
        phone: FakePhoneLock(),
        store: signedInStore(lock: true),
        after: const Duration(seconds: 30),
      );
      await lock.load();
      await lock.unlock();
      final t = DateTime(2026, 10, 9, 12);
      lock.left(t);
      lock.returned(t.add(const Duration(seconds: 10)));
      expect(lock.locked, isFalse);
      lock.left(t);
      lock.returned(t.add(const Duration(seconds: 31)));
      expect(lock.locked, isTrue);
    });

    test('a failed check keeps it locked', () async {
      final phone = FakePhoneLock(passes: false);
      final lock = AppLock(phone: phone, store: signedInStore(lock: true));
      await lock.load();
      expect(await lock.unlock(), isFalse);
      expect(lock.locked, isTrue);
    });
  });

  group('in the app', () {
    testWidgets(
      'with the lock off, nothing is covered and the menu offers it',
      (tester) async {
        await pumpApp(
          tester,
          happyServer(),
          store: signedInStore(),
          phone: FakePhoneLock(),
        );
        expect(find.text('Vyterlix is locked'), findsNothing);
        await tester.tap(find.byTooltip('Account'));
        await tester.pumpAndSettle();
        expect(find.text('Open with fingerprint, face or PIN'), findsOneWidget);
      },
    );

    testWidgets('a phone that cannot check does not see the option', (
      tester,
    ) async {
      await pumpApp(tester, happyServer(), store: signedInStore());
      await tester.tap(find.byTooltip('Account'));
      await tester.pumpAndSettle();
      expect(find.text('Open with fingerprint, face or PIN'), findsNothing);
    });

    testWidgets(
      'turning it on from the menu asks the phone, then keeps the choice',
      (tester) async {
        final store = signedInStore();
        final phone = FakePhoneLock();
        await pumpApp(tester, happyServer(), store: store, phone: phone);
        await tester.tap(find.byTooltip('Account'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Open with fingerprint, face or PIN'));
        await tester.pumpAndSettle();
        expect(phone.reasons, ['Turn on unlocking with your phone lock']);
        expect(store.lockEnabled, isTrue);
        await tester.tap(find.byTooltip('Account'));
        await tester.pumpAndSettle();
        expect(find.text('Stop asking for my phone lock'), findsOneWidget);
      },
    );

    testWidgets(
      'opens locked, asks the phone, and shows the app once it passes',
      (tester) async {
        final phone = FakePhoneLock();
        await pumpApp(
          tester,
          happyServer(),
          store: signedInStore(lock: true),
          phone: phone,
        );
        expect(phone.reasons, ['Unlock Vyterlix']);
        expect(find.text('Vyterlix is locked'), findsNothing);
        expect(find.text('Fakeham Bakery'), findsOneWidget);
      },
    );

    testWidgets('stays locked when the check fails, and can try again', (
      tester,
    ) async {
      final phone = FakePhoneLock(passes: false);
      await pumpApp(
        tester,
        happyServer(),
        store: signedInStore(lock: true),
        phone: phone,
      );
      expect(find.text('Vyterlix is locked'), findsOneWidget);
      phone.passes = true;
      await tester.tap(find.text('Unlock'));
      await tester.pumpAndSettle();
      expect(find.text('Vyterlix is locked'), findsNothing);
    });

    testWidgets('locks again when the app comes back after being away', (
      tester,
    ) async {
      final phone = FakePhoneLock();
      await pumpApp(
        tester,
        happyServer(),
        store: signedInStore(lock: true),
        phone: phone,
        lockAfter: Duration.zero,
      );
      expect(find.text('Vyterlix is locked'), findsNothing);
      phone.passes = false;
      await background(tester);
      expect(find.text('Vyterlix is locked'), findsOneWidget);
      expect(
        find.text('Fakeham Bakery'),
        findsOneWidget,
      ); // still there underneath, but covered
    });

    testWidgets(
      'logging out from the lock screen turns the lock off and signs out',
      (tester) async {
        final store = signedInStore(lock: true);
        await pumpApp(
          tester,
          happyServer(),
          store: store,
          phone: FakePhoneLock(passes: false),
        );
        await tester.tap(find.text('Log out'));
        await tester.pumpAndSettle();
        expect(store.lockEnabled, isFalse);
        expect(store.refreshToken, isNull);
        expect(find.text('Vyterlix is locked'), findsNothing);
        expect(find.widgetWithText(FilledButton, 'Log in'), findsOneWidget);
      },
    );
  });
}
