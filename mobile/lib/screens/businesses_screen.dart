import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../auth/auth_controller.dart';
import '../auth/session_store.dart';
import '../models.dart';
import '../widgets/common.dart';
import 'business_shell.dart';

/// The businesses this person belongs to. The one that was open last time opens straight away.
class BusinessesScreen extends StatefulWidget {
  const BusinessesScreen({super.key, this.openLast = true});

  final bool openLast;

  @override
  State<BusinessesScreen> createState() => _BusinessesScreenState();
}

class _BusinessesScreenState extends State<BusinessesScreen> {
  List<Business>? _businesses;
  String? _error;
  bool _openedLast = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data =
          await context.read<ApiClient>().get('/organizations') as List;
      final list = [
        for (final item in data)
          Business.fromJson(item as Map<String, dynamic>),
      ];
      if (!mounted) return;
      setState(() => _businesses = list);
      await _maybeOpenLast(list);
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _maybeOpenLast(List<Business> list) async {
    if (!widget.openLast || _openedLast) return;
    _openedLast = true;
    final last = await context.read<SessionStore>().readLastBusiness();
    final match = list.where((b) => b.id == last);
    if (match.isNotEmpty && mounted) await _open(match.first);
  }

  Future<void> _open(Business business) async {
    await context.read<SessionStore>().writeLastBusiness(business.id);
    if (!mounted) return;
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => BusinessShell(business: business),
      ),
    );
    // Coming back means "switch business": forget the choice so it is not opened again at once.
    if (mounted) await context.read<SessionStore>().writeLastBusiness(null);
  }

  @override
  Widget build(BuildContext context) {
    final auth = context.watch<AuthController>();
    return Scaffold(
      appBar: AppBar(
        title: const Row(
          children: [
            BrandMark(size: 30),
            SizedBox(width: 10),
            Text('Vyterlix', style: TextStyle(fontWeight: FontWeight.w800)),
          ],
        ),
        actions: [
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
      body: _body(),
    );
  }

  Widget _body() {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final businesses = _businesses;
    if (businesses == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (businesses.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(32),
          child: Text(
            "You don't belong to any business yet. Create one on the Vyterlix website, then come back here.",
            textAlign: TextAlign.center,
          ),
        ),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text(
            'Your businesses',
            style: Theme.of(context).textTheme.headlineSmall
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 4),
          Text(
            'Choose one to open.',
            style: TextStyle(
              color: Theme.of(context).colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(height: 20),
          for (final business in businesses) ...[
            _BusinessCard(business: business, onTap: () => _open(business)),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}

class _BusinessCard extends StatelessWidget {
  const _BusinessCard({required this.business, required this.onTap});

  final Business business;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(16),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            children: [
              BrandMark(size: 44, key: ValueKey('avatar-${business.id}')),
              const SizedBox(width: 14),
              Expanded(
                child: Text(
                  business.name,
                  style: const TextStyle(
                    fontWeight: FontWeight.w700,
                    fontSize: 16,
                  ),
                ),
              ),
              Tag(business.role),
              const SizedBox(width: 4),
              const Icon(Icons.chevron_right_rounded),
            ],
          ),
        ),
      ),
    );
  }
}
