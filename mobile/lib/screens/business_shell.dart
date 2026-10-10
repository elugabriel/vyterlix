import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../auth/auth_controller.dart';
import '../models.dart';
import 'alerts_screen.dart';
import 'notifications_screen.dart';
import 'today_screen.dart';

/// Everything inside one business: the bar at the top and the tabs along the bottom.
class BusinessShell extends StatefulWidget {
  const BusinessShell({super.key, required this.business});

  final Business business;

  @override
  State<BusinessShell> createState() => _BusinessShellState();
}

class _BusinessShellState extends State<BusinessShell> {
  int _tab = 0;
  int _unread = 0;

  /// What each tab has shown so far is kept, so going back and forth does not reload everything.
  late final List<Widget> _tabs = [
    TodayScreen(business: widget.business, onOpenAlerts: () => _go(1)),
    AlertsScreen(business: widget.business),
    NotificationsScreen(
      business: widget.business,
      onUnreadChanged: (count) {
        if (mounted) setState(() => _unread = count);
      },
    ),
  ];

  @override
  void initState() {
    super.initState();
    _loadUnread();
  }

  Future<void> _loadUnread() async {
    try {
      final data = await context.read<ApiClient>().get(
        '/organizations/${widget.business.id}/notifications',
        query: {'limit': '1'},
      );
      if (mounted) {
        setState(
          () => _unread = (data as Map<String, dynamic>)['unread'] as int? ?? 0,
        );
      }
    } on ApiException {
      // The badge is a nicety: the Notifications tab says what went wrong if it matters.
    }
  }

  void _go(int index) => setState(() => _tab = index);

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.business.name,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        actions: [
          IconButton(
            tooltip: 'Switch business',
            icon: const Icon(Icons.swap_horiz_rounded),
            onPressed: () => Navigator.of(context).pop(),
          ),
          PopupMenuButton<String>(
            tooltip: 'Account',
            icon: const Icon(Icons.account_circle_outlined),
            onSelected: (value) {
              if (value == 'logout') auth.logout();
            },
            itemBuilder: (_) => [
              PopupMenuItem<String>(
                enabled: false,
                child: Text(auth.user?.email ?? ''),
              ),
              const PopupMenuItem<String>(
                value: 'logout',
                child: Text('Log out'),
              ),
            ],
          ),
        ],
      ),
      body: IndexedStack(index: _tab, children: _tabs),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: _go,
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.home_outlined),
            selectedIcon: Icon(Icons.home_rounded),
            label: 'Today',
          ),
          const NavigationDestination(
            icon: Icon(Icons.notifications_active_outlined),
            selectedIcon: Icon(Icons.notifications_active_rounded),
            label: 'Alerts',
          ),
          NavigationDestination(
            icon: Badge(
              isLabelVisible: _unread > 0,
              label: Text('$_unread'),
              child: const Icon(Icons.inbox_outlined),
            ),
            selectedIcon: Badge(
              isLabelVisible: _unread > 0,
              label: Text('$_unread'),
              child: const Icon(Icons.inbox_rounded),
            ),
            label: 'Inbox',
          ),
        ],
      ),
    );
  }
}
