import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../widgets/common.dart';
import '../widgets/severity.dart';

/// What the person has been told: unread first in bold, tap one to mark it read.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({
    super.key,
    required this.business,
    this.onUnreadChanged,
  });

  final Business business;

  /// Told how many are unread, so the tab can show a number.
  final ValueChanged<int>? onUnreadChanged;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<AppNotification>? _items;
  int _unread = 0;
  String? _error;

  String get _base => '/organizations/${widget.business.id}/notifications';

  @override
  void initState() {
    super.initState();
    _load();
  }

  void _setUnread(int count) {
    _unread = count;
    widget.onUnreadChanged?.call(count);
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(
        _base,
        query: {'limit': '50'},
      );
      final inbox = Inbox.fromJson(data as Map<String, dynamic>);
      if (!mounted) return;
      setState(() {
        _items = inbox.items;
        _setUnread(inbox.unread);
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _markRead(AppNotification item) async {
    if (item.read) return;
    final api = context.read<ApiClient>();
    setState(() {
      _items = [for (final i in _items!) i.id == item.id ? i.asRead() : i];
      _setUnread(_unread > 0 ? _unread - 1 : 0);
    });
    try {
      await api.post('$_base/${item.id}/read');
    } on ApiException {
      if (mounted) {
        await _load(); // it did not go through: show what the server says
      }
    }
  }

  Future<void> _markAllRead() async {
    final api = context.read<ApiClient>();
    try {
      await api.post('$_base/read-all');
      if (!mounted) return;
      setState(() {
        _items = [for (final i in _items!) i.asRead()];
        _setUnread(0);
      });
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) return ErrorState(message: _error!, onRetry: _load);
    final items = _items;
    if (items == null) return const Center(child: CircularProgressIndicator());
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  _unread == 0 ? 'You are up to date.' : '$_unread unread',
                  style: Theme.of(context).textTheme.titleMedium
                      ?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              if (_unread > 0)
                TextButton(
                  onPressed: _markAllRead,
                  child: const Text('Mark all as read'),
                ),
            ],
          ),
          const SizedBox(height: 8),
          if (items.isEmpty)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 32),
              child: Center(
                child: Text(
                  'Nothing here yet.',
                  style: TextStyle(color: muted),
                ),
              ),
            ),
          for (final item in items) ...[
            Card(
              child: InkWell(
                borderRadius: BorderRadius.circular(16),
                onTap: () => _markRead(item),
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Padding(
                        padding: const EdgeInsets.only(top: 6, right: 12),
                        child: Icon(
                          Icons.circle,
                          key: ValueKey(
                            '${item.read ? 'read' : 'unread'}-${item.id}',
                          ),
                          size: 10,
                          color: item.read
                              ? Colors.transparent
                              : severityColor(item.severity),
                        ),
                      ),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              item.title,
                              style: TextStyle(
                                fontWeight: item.read
                                    ? FontWeight.w500
                                    : FontWeight.w700,
                              ),
                            ),
                            const SizedBox(height: 4),
                            Text(item.body, style: TextStyle(color: muted)),
                            const SizedBox(height: 6),
                            Text(
                              ukDateTime(item.createdAt),
                              style: TextStyle(color: muted, fontSize: 12),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 12),
          ],
        ],
      ),
    );
  }
}
