import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../auth/auth_controller.dart';
import '../format.dart';
import '../models.dart';
import '../widgets/common.dart';

const weekdays = [
  'Monday',
  'Tuesday',
  'Wednesday',
  'Thursday',
  'Friday',
  'Saturday',
  'Sunday',
];

const categoryLabels = {
  'sales': 'Sales',
  'financial': 'Money and cash flow',
  'customer': 'Customers',
  'inventory': 'Stock',
  'marketing': 'Marketing',
  'forecast': 'Forecasts',
  'action': 'Actions and follow-ups',
  'data': 'Data and imports',
  'security': 'Security',
};

const roleLabels = {
  'viewer': 'Viewer (can see everything)',
  'manager': 'Manager',
  'owner': 'Owner',
};

String _hm(String? time) =>
    time == null ? '' : time.substring(0, time.length >= 5 ? 5 : time.length);

/// Business settings, my own notification choices, and the team.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.business});

  final Business business;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  String? _error;
  bool _loaded = false;
  String? _problem;
  String? _note;
  bool _busy = false;

  // business settings
  int _weekStart = 1;
  bool _quietOn = false;
  String _quietStart = '22:00';
  String _quietEnd = '07:00';
  String _footer = '';

  // my notifications: category -> channel -> on, and which are locked
  List<Map<String, dynamic>> _prefs = [];
  final Map<String, Map<String, bool>> _changed = {};

  // team
  List<Map<String, dynamic>> _members = [];
  List<Map<String, dynamic>> _invites = [];
  final _inviteEmail = TextEditingController();
  String _inviteRole = 'viewer';

  bool get _isOwner => widget.business.role == 'owner';
  String get _org => '/organizations/${widget.business.id}';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _inviteEmail.dispose();
    super.dispose();
  }

  List<Map<String, dynamic>> _maps(dynamic data) => [
    for (final i in data as List) i as Map<String, dynamic>,
  ];

  Future<void> _load() async {
    setState(() => _error = null);
    final api = context.read<ApiClient>();
    try {
      final s = await api.get('$_org/settings') as Map<String, dynamic>;
      final prefs = _maps(await api.get('$_org/notification-preferences'));
      final members = _maps(await api.get('$_org/members'));
      var invites = <Map<String, dynamic>>[];
      if (_isOwner) {
        try {
          invites = _maps(await api.get('$_org/invitations'))
              .where((i) => i['status'] == 'pending')
              .toList();
        } on ApiException {
          // the pending list is a convenience
        }
      }
      if (!mounted) return;
      final quiet = s['quiet_hours'] as Map<String, dynamic>?;
      setState(() {
        _weekStart = (s['week_start_day'] as num).toInt();
        _quietOn = quiet != null;
        _quietStart = quiet == null ? '22:00' : _hm('${quiet['start']}');
        _quietEnd = quiet == null ? '07:00' : _hm('${quiet['end']}');
        _footer =
            'Times are UK time (${s['timezone']}); dates show as ${s['date_format']}; money in ${s['currency']}.';
        _prefs = prefs;
        _changed.clear();
        _members = members;
        _invites = invites;
        _loaded = true;
      });
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _act(
    Future<void> Function(ApiClient api) action,
    String done, {
    bool reload = false,
  }) async {
    setState(() {
      _busy = true;
      _problem = null;
      _note = null;
    });
    try {
      await action(context.read<ApiClient>());
      if (reload) await _load();
      if (mounted) setState(() => _note = done);
    } on ApiException catch (error) {
      if (mounted) setState(() => _problem = error.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pickTime(bool start) async {
    final current = start ? _quietStart : _quietEnd;
    final parts = current.split(':');
    final picked = await showTimePicker(
      context: context,
      initialTime: TimeOfDay(
        hour: int.parse(parts[0]),
        minute: int.parse(parts[1]),
      ),
    );
    if (picked == null) return;
    final text =
        '${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}';
    setState(() => start ? _quietStart = text : _quietEnd = text);
  }

  Future<void> _saveSettings() => _act((api) async {
    await api.patch(
      '$_org/settings',
      body: {
        'week_start_day': _weekStart,
        'quiet_hours': _quietOn
            ? {'start': _quietStart, 'end': _quietEnd}
            : null,
      },
    );
  }, 'Settings saved.');

  bool _value(Map<String, dynamic> p, String channel) =>
      _changed[p['category']]?[channel] ?? (p[channel] == true);

  Future<void> _saveNotifications() => _act(
    (api) async {
      await api.patch(
        '$_org/notification-preferences',
        body: {'preferences': _changed},
      );
    },
    'Your notification choices are saved.',
    reload: true,
  );

  Future<void> _invite() async {
    final email = _inviteEmail.text.trim();
    if (!email.contains('@')) {
      setState(() => _problem = 'Type the email address of the person.');
      return;
    }
    await _act(
      (api) async {
        await api.post(
          '$_org/invitations',
          body: {'email': email, 'role': _inviteRole},
        );
        _inviteEmail.clear();
      },
      'Invitation sent to $email.',
      reload: true,
    );
  }

  Future<void> _cancelInvite(Map<String, dynamic> invite) async {
    final yes = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Cancel the invitation to ${invite['email']}?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep it'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Cancel it'),
          ),
        ],
      ),
    );
    if (yes != true) return;
    await _act(
      (api) async {
        await api.delete('$_org/invitations/${invite['id']}');
      },
      'Invitation cancelled.',
      reload: true,
    );
  }

  Future<void> _changeRole(Map<String, dynamic> member, String role) => _act(
    (api) async {
      await api.patch(
        '$_org/members/${member['user_id']}',
        body: {'role': role},
      );
    },
    '${member['full_name']} is now ${roleLabels[role]?.split(' (').first.toLowerCase()}.',
    reload: true,
  );

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Settings',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : !_loaded
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(20),
                children: [
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
                  _businessCard(muted),
                  _notificationsCard(muted),
                  _teamCard(muted),
                ],
              ),
            ),
    );
  }

  Widget _businessCard(Color muted) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('Business settings'),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(_footer, style: TextStyle(color: muted)),
                const SizedBox(height: 12),
                DropdownButtonFormField<int>(
                  key: const ValueKey('week-start'),
                  initialValue: _weekStart,
                  decoration: const InputDecoration(
                    labelText: 'Weeks start on',
                  ),
                  items: [
                    for (var i = 0; i < 7; i++)
                      DropdownMenuItem(value: i + 1, child: Text(weekdays[i])),
                  ],
                  onChanged: _isOwner
                      ? (v) => setState(() => _weekStart = v ?? 1)
                      : null,
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Hold back non-urgent alerts during quiet hours',
                  ),
                  value: _quietOn,
                  onChanged: _isOwner
                      ? (v) => setState(() => _quietOn = v)
                      : null,
                ),
                if (_quietOn)
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton(
                          key: const ValueKey('quiet-start'),
                          onPressed: _isOwner ? () => _pickTime(true) : null,
                          child: Text('From $_quietStart'),
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: OutlinedButton(
                          key: const ValueKey('quiet-end'),
                          onPressed: _isOwner ? () => _pickTime(false) : null,
                          child: Text('Until $_quietEnd'),
                        ),
                      ),
                    ],
                  ),
                const SizedBox(height: 12),
                if (_isOwner)
                  FilledButton(
                    onPressed: _busy ? null : _saveSettings,
                    child: const Text('Save settings'),
                  )
                else
                  Text(
                    'Only an owner can change these.',
                    style: TextStyle(color: muted),
                  ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _notificationsCard(Color muted) {
    const channels = [
      ('email', 'Email'),
      ('in_app', 'In the app'),
      ('push', 'Phone'),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('My notifications'),
        Text('Just for you, in this business.', style: TextStyle(color: muted)),
        const SizedBox(height: 8),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                for (final p in _prefs) ...[
                  Text(
                    categoryLabels[p['category']] ?? '${p['category']}',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  Wrap(
                    spacing: 8,
                    children: [
                      for (final (channel, label) in channels)
                        FilterChip(
                          key: ValueKey('pref-${p['category']}-$channel'),
                          label: Text(label),
                          selected: _value(p, channel),
                          onSelected: p['locked'] == true && channel != 'push'
                              ? null
                              : (on) => setState(() {
                                  (_changed[p['category'] as String] ??=
                                          {})[channel] =
                                      on;
                                }),
                        ),
                    ],
                  ),
                  const SizedBox(height: 10),
                ],
                Text(
                  'Security alerts always come by email and in the app, to keep your account safe.',
                  style: TextStyle(color: muted, fontSize: 13),
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: _busy || _changed.isEmpty
                      ? null
                      : _saveNotifications,
                  child: const Text('Save my notifications'),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _teamCard(Color muted) {
    final me = context.read<AuthController>().user?.email;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('Your team'),
        for (final m in _members)
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(
                          '${m['full_name']}',
                          style: const TextStyle(fontWeight: FontWeight.w700),
                        ),
                      ),
                      if (m['status'] != 'active') Tag('${m['status']}'),
                    ],
                  ),
                  Text(
                    '${m['email']} · joined ${ukDate('${m['joined_at']}'.substring(0, 10))}',
                    style: TextStyle(color: muted, fontSize: 13),
                  ),
                  const SizedBox(height: 8),
                  if (_isOwner && m['email'] != me)
                    DropdownButton<String>(
                      key: ValueKey('role-${m['user_id']}'),
                      value: '${m['role']}',
                      isExpanded: true,
                      items: [
                        for (final r in roleLabels.keys)
                          DropdownMenuItem(
                            value: r,
                            child: Text(roleLabels[r]!),
                          ),
                      ],
                      onChanged: _busy
                          ? null
                          : (r) {
                              if (r != null && r != m['role']) {
                                _changeRole(m, r);
                              }
                            },
                    )
                  else
                    Text(
                      roleLabels['${m['role']}'] ?? '${m['role']}',
                      style: TextStyle(color: muted),
                    ),
                ],
              ),
            ),
          ),
        if (_invites.isNotEmpty) ...[
          const SectionTitle('Invited, not joined yet'),
          for (final i in _invites)
            Card(
              child: ListTile(
                title: Text('${i['email']}'),
                subtitle: Text(roleLabels['${i['role']}'] ?? '${i['role']}'),
                trailing: TextButton(
                  onPressed: _busy ? null : () => _cancelInvite(i),
                  child: const Text('Cancel'),
                ),
              ),
            ),
        ],
        if (_isOwner) ...[
          const SectionTitle('Invite someone'),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                children: [
                  TextField(
                    controller: _inviteEmail,
                    keyboardType: TextInputType.emailAddress,
                    autocorrect: false,
                    decoration: const InputDecoration(labelText: 'Email'),
                  ),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                    key: const ValueKey('invite-role'),
                    initialValue: _inviteRole,
                    decoration: const InputDecoration(labelText: 'Role'),
                    items: [
                      for (final r in roleLabels.keys)
                        DropdownMenuItem(value: r, child: Text(roleLabels[r]!)),
                    ],
                    onChanged: (v) =>
                        setState(() => _inviteRole = v ?? 'viewer'),
                  ),
                  const SizedBox(height: 12),
                  FilledButton(
                    onPressed: _busy ? null : _invite,
                    child: const Text('Send invitation'),
                  ),
                ],
              ),
            ),
          ),
        ],
      ],
    );
  }
}
