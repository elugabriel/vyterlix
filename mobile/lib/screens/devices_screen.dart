import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../widgets/common.dart';

/// Every phone and browser signed in to this account. Anyone can end the others.
class DevicesScreen extends StatefulWidget {
  const DevicesScreen({super.key});

  @override
  State<DevicesScreen> createState() => _DevicesScreenState();
}

class _DevicesScreenState extends State<DevicesScreen> {
  List<Map<String, dynamic>>? _sessions;
  String? _error;
  String? _problem;
  String? _note;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get('/me/sessions');
      if (mounted) {
        setState(
          () => _sessions = [
            for (final s in data as List) s as Map<String, dynamic>,
          ],
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<bool> _confirm(String title, String yes) async =>
      await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: Text(title),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Keep it'),
            ),
            TextButton(
              onPressed: () => Navigator.pop(context, true),
              child: Text(yes),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> _act(
    Future<void> Function(ApiClient api) action,
    String done,
  ) async {
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      await action(context.read<ApiClient>());
      await _load();
      if (mounted) setState(() => _note = done);
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _title(Map<String, dynamic> s) =>
      '${s['device_name'] ?? (s['client'] == 'mobile' ? 'A phone' : 'A web browser')}';

  /// Ask for a name and give it to the device, so it is easy to spot ("Jo's iPhone").
  Future<void> _rename(Map<String, dynamic> s) async {
    final name = await showDialog<String>(
      context: context,
      builder: (context) => _NameDialog(start: '${s['device_name'] ?? ''}'),
    );
    if (name == null || name.isEmpty) return;
    await _act((api) async {
      await api.patch('/me/sessions/${s['id']}', body: {'device_name': name});
    }, 'Named it $name.');
  }

  Future<void> _end(Map<String, dynamic> s) async {
    final name = _title(s);
    if (!await _confirm('Sign out of $name?', 'Sign it out')) return;
    await _act((api) async {
      await api.delete('/me/sessions/${s['id']}');
    }, 'Signed out of $name.');
  }

  Future<void> _endOthers() async {
    if (!await _confirm('Sign out of every other device?', 'Sign them out')) {
      return;
    }
    await _act((api) async {
      await api.post('/me/sessions/revoke-others');
    }, 'Every other device has been signed out.');
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final sessions = _sessions;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Where you are signed in',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : sessions == null
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  Text(
                    'If you do not recognise one of these, sign it out and change your password.',
                    style: TextStyle(color: muted),
                  ),
                  const SizedBox(height: 12),
                  if (_problem != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(
                        _problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    ),
                  if (_note != null)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: Text(_note!),
                    ),
                  for (final s in sessions)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Wrap(
                              spacing: 8,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                Text(
                                  _title(s),
                                  style: const TextStyle(
                                    fontWeight: FontWeight.w700,
                                  ),
                                ),
                                if (s['current'] == true)
                                  const Tag('This device'),
                              ],
                            ),
                            const SizedBox(height: 4),
                            Text(
                              s['last_used_at'] == null
                                  ? 'Signed in ${ukDateTime('${s['created_at']}')}'
                                  : 'Last used ${ukDateTime('${s['last_used_at']}')}',
                              style: TextStyle(color: muted, fontSize: 13),
                            ),
                            Wrap(
                              children: [
                                TextButton(
                                  key: ValueKey('name-${s['id']}'),
                                  onPressed: _busy ? null : () => _rename(s),
                                  child: const Text('Give it a name'),
                                ),
                                if (s['current'] != true)
                                  TextButton(
                                    key: ValueKey('end-${s['id']}'),
                                    onPressed: _busy ? null : () => _end(s),
                                    child: const Text('Sign it out'),
                                  ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  if (sessions.length > 1)
                    OutlinedButton(
                      onPressed: _busy ? null : _endOthers,
                      child: const Text('Sign out of every other device'),
                    ),
                ],
              ),
            ),
    );
  }
}

class _NameDialog extends StatefulWidget {
  const _NameDialog({required this.start});

  final String start;

  @override
  State<_NameDialog> createState() => _NameDialogState();
}

class _NameDialogState extends State<_NameDialog> {
  late final _controller = TextEditingController(text: widget.start);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('Name this device'),
      content: TextField(
        controller: _controller,
        maxLength: 100,
        autofocus: true,
        decoration: const InputDecoration(
          labelText: "For example: Jo's iPhone",
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, _controller.text.trim()),
          child: const Text('Save name'),
        ),
      ],
    );
  }
}
