// The assistant: questions, answers and the choices about AI, as the server sends them.

class Source {
  const Source({required this.label, required this.link});

  factory Source.fromJson(Map<String, dynamic> json) =>
      Source(label: '${json['label']}', link: json['link'] as String?);

  final String label;
  final String? link;
}

class ToolCall {
  const ToolCall({
    required this.tool,
    required this.ok,
    required this.factCount,
  });

  factory ToolCall.fromJson(Map<String, dynamic> json) => ToolCall(
    tool: '${json['tool']}',
    ok: json['ok'] == true,
    factCount: (json['facts'] as List? ?? const []).length,
  );

  final String tool;
  final bool ok;
  final int factCount;
}

class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.isMine,
    required this.content,
    required this.sources,
    required this.engine,
    required this.sentOutside,
    required this.toolCalls,
    required this.createdAt,
  });

  factory ChatMessage.fromJson(Map<String, dynamic> json) => ChatMessage(
    id: '${json['id']}',
    isMine: json['role'] == 'user',
    content: '${json['content']}',
    sources: [
      for (final s in (json['sources'] as List? ?? const []))
        Source.fromJson(s as Map<String, dynamic>),
    ],
    engine: json['engine'] as String?,
    sentOutside: json['sent_outside'] == true,
    toolCalls: [
      for (final c in (json['tool_calls'] as List? ?? const []))
        ToolCall.fromJson(c as Map<String, dynamic>),
    ],
    createdAt: '${json['created_at']}',
  );

  /// A question that has been typed but not yet answered.
  factory ChatMessage.pending(String text) => ChatMessage(
    id: 'pending',
    isMine: true,
    content: text,
    sources: const [],
    engine: null,
    sentOutside: false,
    toolCalls: const [],
    createdAt: DateTime.now().toIso8601String(),
  );

  final String id;
  final bool isMine;
  final String content;
  final List<Source> sources;
  final String? engine;

  /// Whether the facts behind this answer were sent to an outside AI provider.
  final bool sentOutside;
  final List<ToolCall> toolCalls;
  final String createdAt;
}

class Answer {
  const Answer({
    required this.conversationId,
    required this.question,
    required this.answer,
  });

  factory Answer.fromJson(Map<String, dynamic> json) => Answer(
    conversationId: '${json['conversation_id']}',
    question: ChatMessage.fromJson(json['question'] as Map<String, dynamic>),
    answer: ChatMessage.fromJson(json['answer'] as Map<String, dynamic>),
  );

  final String conversationId;
  final ChatMessage question;
  final ChatMessage answer;
}

class Conversation {
  const Conversation({
    required this.id,
    required this.title,
    required this.messageCount,
    required this.lastMessageAt,
  });

  factory Conversation.fromJson(Map<String, dynamic> json) => Conversation(
    id: '${json['id']}',
    title: '${json['title']}',
    messageCount: (json['message_count'] as int?) ?? 0,
    lastMessageAt: '${json['last_message_at']}',
  );

  final String id;
  final String title;
  final int messageCount;
  final String lastMessageAt;
}

class AiSettings {
  const AiSettings({
    required this.aiEnabled,
    required this.allowExternalAi,
    required this.allowModelImprovement,
    required this.externalProviderAvailable,
  });

  factory AiSettings.fromJson(Map<String, dynamic> json) => AiSettings(
    aiEnabled: json['ai_enabled'] == true,
    allowExternalAi: json['allow_external_ai'] == true,
    allowModelImprovement: json['allow_model_improvement'] == true,
    externalProviderAvailable: json['external_provider_available'] == true,
  );

  final bool aiEnabled;
  final bool allowExternalAi;
  final bool allowModelImprovement;
  final bool externalProviderAvailable;
}
