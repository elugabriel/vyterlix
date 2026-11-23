import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'auth/auth_controller.dart';
import 'auth/session_store.dart';
import 'cache.dart';
import 'files.dart';
import 'lock.dart';
import 'screens/businesses_screen.dart';
import 'screens/login_screen.dart';
import 'screens/welcome_screen.dart';
import 'theme.dart';
import 'widgets/common.dart';
import 'widgets/lock_layer.dart';
import 'widgets/offline_banner.dart';

class VyterlixApp extends StatelessWidget {
  const VyterlixApp({
    super.key,
    required this.auth,
    required this.api,
    required this.store,
    required this.lock,
    required this.offline,
    this.files = const PhoneFileChooser(),
    this.showWelcome = false,
  });

  final AuthController auth;
  final ApiClient api;
  final SessionStore store;
  final AppLock lock;
  final OfflineState offline;

  /// How a spreadsheet is chosen on this phone (tests give it a made-up one).
  final FileChooser files;

  /// Whether someone who is signed out first sees the welcome screen (the real app does; tests go straight to login).
  final bool showWelcome;

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthController>.value(value: auth),
        Provider<ApiClient>.value(value: api),
        Provider<SessionStore>.value(value: store),
        ChangeNotifierProvider<AppLock>.value(value: lock),
        ChangeNotifierProvider<OfflineState>.value(value: offline),
        Provider<FileChooser>.value(value: files),
      ],
      child: MaterialApp(
        title: 'Vyterlix',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        builder: (context, child) =>
            LockLayer(child: OfflineBanner(child: child ?? const SizedBox())),
        home: _Gate(showWelcome: showWelcome),
      ),
    );
  }
}

/// Shows the screen that fits whether someone is signed in.
class _Gate extends StatelessWidget {
  const _Gate({required this.showWelcome});

  final bool showWelcome;

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    switch (auth.status) {
      case AuthStatus.starting:
        return const Scaffold(body: Center(child: BrandMark(size: 64)));
      case AuthStatus.unreachable:
        return Scaffold(
          body: ErrorState(
            message: 'Could not reach Vyterlix. Check your connection and try again.',
            onRetry: auth.start,
          ),
        );
      case AuthStatus.signedOut:
        // Whatever screen was open (the server may have ended the sign-in from anywhere), go back to this one.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (context.mounted) {
            Navigator.of(context).popUntil((route) => route.isFirst);
          }
        });
        return showWelcome ? const _SignedOut() : const LoginScreen();
      case AuthStatus.signedIn:
        return const BusinessesScreen();
    }
  }
}

/// The welcome screen once per launch of the app, then the login screen (also after the server ends a sign-in).
class _SignedOut extends StatefulWidget {
  const _SignedOut();

  @override
  State<_SignedOut> createState() => _SignedOutState();
}

class _SignedOutState extends State<_SignedOut> {
  static bool _welcomed = false;

  @override
  Widget build(BuildContext context) {
    if (_welcomed) return const LoginScreen();
    return WelcomeScreen(onContinue: () => setState(() => _welcomed = true));
  }
}
