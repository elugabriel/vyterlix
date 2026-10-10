import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../auth/auth_controller.dart';
import '../lock.dart';
import 'common.dart';

/// Sits over the whole app: notices when the app goes out of sight and comes back, and covers
/// everything with the lock screen when the person has asked for the phone lock and it is time.
class LockLayer extends StatefulWidget {
  const LockLayer({super.key, required this.child});

  final Widget child;

  @override
  State<LockLayer> createState() => _LockLayerState();
}

class _LockLayerState extends State<LockLayer> with WidgetsBindingObserver {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    final lock = context.read<AppLock>();
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.hidden) {
      lock.left();
    } else if (state == AppLifecycleState.resumed) {
      lock.returned();
    }
  }

  @override
  Widget build(BuildContext context) {
    final lock = context.watch<AppLock>();
    final signedIn =
        context.watch<AuthController>().status == AuthStatus.signedIn;
    return Stack(
      children: [
        widget.child,
        if (lock.locked && signedIn) const Positioned.fill(child: LockScreen()),
      ],
    );
  }
}

class LockScreen extends StatefulWidget {
  const LockScreen({super.key});

  @override
  State<LockScreen> createState() => _LockScreenState();
}

class _LockScreenState extends State<LockScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _unlock();
    });
  }

  Future<void> _unlock() => context.read<AppLock>().unlock();

  Future<void> _logOut() async {
    final lock = context.read<AppLock>();
    final auth = context.read<AuthController>();
    await lock.disable();
    await auth.logout();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const BrandMark(size: 64),
                const SizedBox(height: 24),
                const Text(
                  'Vyterlix is locked',
                  style: TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
                ),
                const SizedBox(height: 8),
                Text(
                  'Use your fingerprint, face or phone PIN to open it.',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
                ),
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: _unlock,
                  icon: const Icon(Icons.fingerprint_rounded),
                  label: const Text('Unlock'),
                ),
                TextButton(onPressed: _logOut, child: const Text('Log out')),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
