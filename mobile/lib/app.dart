import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'auth/auth_controller.dart';
import 'auth/session_store.dart';
import 'screens/businesses_screen.dart';
import 'screens/login_screen.dart';
import 'theme.dart';
import 'widgets/common.dart';

class VyterlixApp extends StatelessWidget {
  const VyterlixApp({
    super.key,
    required this.auth,
    required this.api,
    required this.store,
  });

  final AuthController auth;
  final ApiClient api;
  final SessionStore store;

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider<AuthController>.value(value: auth),
        Provider<ApiClient>.value(value: api),
        Provider<SessionStore>.value(value: store),
      ],
      child: MaterialApp(
        title: 'Vyterlix',
        debugShowCheckedModeBanner: false,
        theme: buildTheme(Brightness.light),
        darkTheme: buildTheme(Brightness.dark),
        home: const _Gate(),
      ),
    );
  }
}

/// Shows the screen that fits whether someone is signed in.
class _Gate extends StatelessWidget {
  const _Gate();

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
        return const LoginScreen();
      case AuthStatus.signedIn:
        return const BusinessesScreen();
    }
  }
}
