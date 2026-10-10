import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../auth/auth_controller.dart';
import '../lock.dart';
import '../screens/devices_screen.dart';

/// The person icon in the top bar: who is signed in, the devices, and log out.
class AccountMenu extends StatelessWidget {
  const AccountMenu({super.key});

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    final lock = context.watch<AppLock>();
    return PopupMenuButton<String>(
      tooltip: 'Account',
      icon: const Icon(Icons.account_circle_outlined),
      onSelected: (value) {
        if (value == 'logout') {
          lock.disable();
          auth.logout();
        }
        if (value == 'lock') {
          if (lock.enabled) {
            lock.disable();
          } else {
            lock.enable();
          }
        }
        if (value == 'devices') {
          Navigator.of(context).push(
            MaterialPageRoute<void>(builder: (_) => const DevicesScreen()),
          );
        }
      },
      itemBuilder: (_) => [
        PopupMenuItem<String>(
          enabled: false,
          child: Text(auth.user?.email ?? ''),
        ),
        const PopupMenuItem<String>(
          value: 'devices',
          child: Text('Where you are signed in'),
        ),
        if (lock.supported)
          PopupMenuItem<String>(
            value: 'lock',
            child: Text(
              lock.enabled
                  ? 'Stop asking for my phone lock'
                  : 'Open with fingerprint, face or PIN',
            ),
          ),
        const PopupMenuItem<String>(value: 'logout', child: Text('Log out')),
      ],
    );
  }
}
