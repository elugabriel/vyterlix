import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../format.dart';
import '../models.dart';
import '../models_assistant.dart';
import '../theme.dart';
import '../widgets/common.dart';

const _examples = [
  'How are we doing?',
  'What were my sales last month?',
  'Why did sales fall?',
  'What should I do about it?',
  'Forecast my sales',
  'What are my actions?',
  'Did it work?',
];

/// Ask a question about the business. Answers come only from its own results, say where they came
/// from, and never guess; a question that cannot be answered from its records is told so.
class AskScreen extends StatefulWidget {
  const AskScreen({super.key, required this.business});

  final Business business;

  @override
  State<AskScreen> createState() => _AskScreenState();
}

class _AskScreenState extends State<AskScreen> {
  final _box = TextEditingController();
  final _scroll = ScrollController();
  final List<ChatMessage> _thread = [];
  String? _conversationId;
  bool _asking = false;

  String get _base => '/organizations/${widget.business.id}/assistant';

  @override
  void dispose() {
    _box.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _toEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) {
        _scroll.animateTo(
          _scroll.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  Future<void> _ask(String text) async {
    final question = text.trim();
    if (question.isEmpty || _asking) return;
    final api = context.read<ApiClient>();
    setState(() {
      _asking = true;
      _thread.add(ChatMessage.pending(question));
      _box.clear();
    });
    _toEnd();
    try {
      final data = await api.post(
        '$_base/ask',
        body: {'message': question, 'conversation_id': _conversationId},
      );
      final answer = Answer.fromJson(data as Map<String, dynamic>);
      if (!mounted) return;
      setState(() {
        _conversationId = answer.conversationId;
        _thread
          ..removeLast()
          ..addAll([answer.question, answer.answer]);
      });
      _toEnd();
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() {
        _thread.removeLast();
        _box.text = question; // so nothing typed is lost
      });
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text(error.message)));
    } finally {
      if (mounted) setState(() => _asking = false);
    }
  }

  void _newConversation() => setState(() {
    _conversationId = null;
    _thread.clear();
  });

  Future<void> _earlier() async {
    final opened = await Navigator.of(context).push<_Opened>(
      MaterialPageRoute(
        builder: (_) => _ConversationsScreen(
          business: widget.business,
          current: _conversationId,
        ),
      ),
    );
    if (opened == null || !mounted) return;
    setState(() {
      _conversationId = opened.id;
      _thread
        ..clear()
        ..addAll(opened.messages);
    });
    _toEnd();
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Ask Vyterlix',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
        actions: [
          IconButton(
            tooltip: 'New conversation',
            icon: const Icon(Icons.add_comment_outlined),
            onPressed: _newConversation,
          ),
          PopupMenuButton<String>(
            onSelected: (value) {
              if (value == 'earlier') _earlier();
              if (value == 'ai') {
                Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => AiSettingsScreen(business: widget.business),
                  ),
                );
              }
            },
            itemBuilder: (_) => const [
              PopupMenuItem(
                value: 'earlier',
                child: Text('Earlier conversations'),
              ),
              PopupMenuItem(value: 'ai', child: Text('AI and your data')),
            ],
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: _thread.isEmpty
                ? ListView(
                    padding: const EdgeInsets.all(20),
                    children: [
                      Text(
                        'Ask a question about your business. Answers come only from your own figures and records, say where they came from, and never guess. If something cannot be answered from your records, you will be told so.',
                        style: TextStyle(color: muted),
                      ),
                      const SectionTitle('Try asking'),
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: [
                          for (final q in _examples)
                            ActionChip(
                              label: Text(q),
                              onPressed: () => _ask(q),
                            ),
                        ],
                      ),
                    ],
                  )
                : ListView(
                    controller: _scroll,
                    padding: const EdgeInsets.all(20),
                    children: [
                      for (final m in _thread) ...[
                        _Bubble(message: m),
                        const SizedBox(height: 12),
                      ],
                      if (_asking)
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Padding(
                            padding: const EdgeInsets.all(8),
                            child: Text(
                              'Looking at your results...',
                              key: const ValueKey('typing'),
                              style: TextStyle(color: muted),
                            ),
                          ),
                        ),
                    ],
                  ),
          ),
          SafeArea(
            top: false,
            child: Container(
              padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
              decoration: BoxDecoration(
                color: Theme.of(context).appBarTheme.backgroundColor,
                border: Border(
                  top: BorderSide(
                    color: Theme.of(context).colorScheme.outlineVariant,
                  ),
                ),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _box,
                      maxLength: 600,
                      minLines: 1,
                      maxLines: 4,
                      textInputAction: TextInputAction.send,
                      onSubmitted: _ask,
                      decoration: const InputDecoration(
                        hintText: 'Ask about your business...',
                        counterText: '',
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    tooltip: 'Ask',
                    onPressed: _asking ? null : () => _ask(_box.text),
                    icon: const Icon(Icons.arrow_upward_rounded),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  const _Bubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    if (message.isMine) {
      return Align(
        alignment: Alignment.centerRight,
        child: ConstrainedBox(
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.82,
          ),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            decoration: BoxDecoration(
              gradient: Palette.gradient,
              borderRadius: BorderRadius.circular(16),
            ),
            child: Text(
              message.content,
              style: const TextStyle(color: Colors.white),
            ),
          ),
        ),
      );
    }
    final engine = message.engine == 'vyterlix-grounded'
        ? 'Vyterlix, from your own results'
        : message.engine;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final line in message.content.split('\n'))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(line),
              ),
            if (message.sources.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text(
                  'From: ${message.sources.map((s) => s.label).join(', ')}',
                  style: TextStyle(color: muted, fontSize: 13),
                ),
              ),
            const SizedBox(height: 4),
            Text(
              'Answered by $engine${message.sentOutside ? '. The facts behind this answer were sent to an outside AI provider, as you allowed.' : '. Nothing left Vyterlix.'}',
              style: TextStyle(color: muted, fontSize: 12),
            ),
            if (message.toolCalls.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                shape: const Border(),
                collapsedShape: const Border(),
                title: Text(
                  'What was looked up',
                  style: TextStyle(color: muted, fontSize: 13),
                ),
                children: [
                  for (final c in message.toolCalls)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        '${c.tool}: ${c.ok ? '${c.factCount} fact(s) found' : 'nothing found'}',
                        style: TextStyle(color: muted, fontSize: 12),
                      ),
                    ),
                ],
              ),
          ],
        ),
      ),
    );
  }
}

class _Opened {
  const _Opened(this.id, this.messages);

  final String id;
  final List<ChatMessage> messages;
}

/// The conversations the person has had, to carry on with or delete.
class _ConversationsScreen extends StatefulWidget {
  const _ConversationsScreen({required this.business, required this.current});

  final Business business;
  final String? current;

  @override
  State<_ConversationsScreen> createState() => _ConversationsScreenState();
}

class _ConversationsScreenState extends State<_ConversationsScreen> {
  List<Conversation>? _list;
  String? _error;

  String get _base =>
      '/organizations/${widget.business.id}/assistant/conversations';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(_base);
      if (mounted) {
        setState(
          () => _list = [
            for (final c in data as List)
              Conversation.fromJson(c as Map<String, dynamic>),
          ],
        );
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _open(Conversation c) async {
    final navigator = Navigator.of(context);
    try {
      final data = await context.read<ApiClient>().get(
        '$_base/${c.id}',
      ) as Map<String, dynamic>;
      navigator.pop(
        _Opened(c.id, [
          for (final m in data['messages'] as List)
            ChatMessage.fromJson(m as Map<String, dynamic>),
        ]),
      );
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  Future<void> _delete(Conversation c) async {
    final sure = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('Delete this conversation?'),
        content: const Text('This cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('Keep it'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            style: FilledButton.styleFrom(minimumSize: const Size(120, 44)),
            child: const Text('Delete it'),
          ),
        ],
      ),
    );
    if (sure != true || !mounted) return;
    try {
      await context.read<ApiClient>().delete('$_base/${c.id}');
      if (mounted) await _load();
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Earlier conversations',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : _list == null
          ? const Center(child: CircularProgressIndicator())
          : _list!.isEmpty
          ? Center(
              child: Text('None yet.', style: TextStyle(color: muted)),
            )
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                for (final c in _list!) ...[
                  Card(
                    child: ListTile(
                      onTap: () => _open(c),
                      title: Text(
                        c.title,
                        style: const TextStyle(fontWeight: FontWeight.w700),
                      ),
                      subtitle: Text(
                        '${c.messageCount ~/ 2} question(s), ${ukDateTime(c.lastMessageAt)}',
                      ),
                      trailing: IconButton(
                        tooltip: 'Delete',
                        icon: const Icon(Icons.delete_outline_rounded),
                        onPressed: () => _delete(c),
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

/// What the owner decides about AI and the business's data.
class AiSettingsScreen extends StatefulWidget {
  const AiSettingsScreen({super.key, required this.business});

  final Business business;

  @override
  State<AiSettingsScreen> createState() => _AiSettingsScreenState();
}

class _AiSettingsScreenState extends State<AiSettingsScreen> {
  AiSettings? _settings;
  bool _enabled = true;
  bool _outside = false;
  bool _improve = false;
  bool _saving = false;
  String? _error;

  String get _path => '/organizations/${widget.business.id}/assistant/settings';

  bool get _isOwner => widget.business.role == 'owner';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _error = null);
    try {
      final data = await context.read<ApiClient>().get(_path);
      final s = AiSettings.fromJson(data as Map<String, dynamic>);
      if (mounted) {
        setState(() {
          _settings = s;
          _enabled = s.aiEnabled;
          _outside = s.allowExternalAi;
          _improve = s.allowModelImprovement;
        });
      }
    } on ApiException catch (error) {
      if (mounted) setState(() => _error = error.message);
    }
  }

  Future<void> _save() async {
    setState(() => _saving = true);
    try {
      await context.read<ApiClient>().put(
        _path,
        body: {
          'ai_enabled': _enabled,
          'allow_external_ai': _outside,
          'allow_model_improvement': _improve,
        },
      );
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Saved')));
      }
    } on ApiException catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(error.message)));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    final s = _settings;
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'AI and your data',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
      ),
      body: _error != null
          ? ErrorState(message: _error!, onRetry: _load)
          : s == null
          ? const Center(child: CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text(
                  s.externalProviderAvailable
                      ? 'By default nothing leaves Vyterlix: answers are put together from your own results. If you allow it, only the question and the short list of facts found for it are sent to the outside provider, never your records, and what comes back is checked against those facts before you see it.'
                      : 'No outside AI provider is set up on this installation, so every answer is put together by Vyterlix itself and nothing leaves it.',
                  style: TextStyle(color: muted),
                ),
                const SizedBox(height: 12),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'The assistant is switched on for this business',
                  ),
                  value: _enabled,
                  onChanged: _isOwner
                      ? (v) => setState(() => _enabled = v)
                      : null,
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Allow an outside AI provider to reword answers',
                  ),
                  value: _outside,
                  onChanged: _isOwner && s.externalProviderAvailable
                      ? (v) => setState(() => _outside = v)
                      : null,
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text(
                    'Allow my conversations to be used to improve AI models',
                  ),
                  value: _improve,
                  onChanged: _isOwner
                      ? (v) => setState(() => _improve = v)
                      : null,
                ),
                const SizedBox(height: 12),
                if (_isOwner)
                  FilledButton(
                    onPressed: _saving ? null : _save,
                    child: const Text('Save'),
                  )
                else
                  Text(
                    'Only the owner can change these.',
                    style: TextStyle(color: muted),
                  ),
              ],
            ),
    );
  }
}
