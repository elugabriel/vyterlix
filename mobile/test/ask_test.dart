import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models_assistant.dart';

import 'support.dart';

Map<String, dynamic> messageJson(
  String id,
  String role,
  String content, {
  bool outside = false,
  List<Map<String, dynamic>> sources = const [],
  List<Map<String, dynamic>> calls = const [],
}) => {
  'id': id,
  'role': role,
  'content': content,
  'intent': null,
  'sources': sources,
  'engine': role == 'assistant' ? 'vyterlix-grounded' : null,
  'sent_outside': outside,
  'tool_calls': calls,
  'created_at': '2026-10-09T13:05:00',
};

Map<String, dynamic> answerJson(
  String question, {
  String conversation = 'c1',
  bool outside = false,
}) => {
  'conversation_id': conversation,
  'title': question,
  'question': messageJson('q-$question', 'user', question),
  'answer': messageJson(
    'a-$question',
    'assistant',
    'Your sales were £8,717.10 in September.\nThat is up 9.1% on August.',
    outside: outside,
    sources: [
      {
        'kind': 'kpi',
        'label': 'Sales for September 2026',
        'ref': 'sales',
        'link': 'kpis.html#sales',
      },
      {'kind': 'kpi', 'label': 'Costs', 'ref': 'costs', 'link': null},
    ],
    calls: [
      {
        'tool': 'kpi_value',
        'arguments': {},
        'ok': true,
        'facts': ['a', 'b'],
        'duration_ms': 3,
      },
      {
        'tool': 'forecast',
        'arguments': {},
        'ok': false,
        'facts': [],
        'duration_ms': 2,
      },
    ],
  ),
};

FakeServer askServer() {
  final server = happyServer()
    ..on(
      'POST /organizations/b1/assistant/ask',
      (request) =>
          jsonResponse(answerJson(jsonBodyOf(request)['message'] as String)),
    )
    ..json('GET /organizations/b1/assistant/conversations', [
      {
        'id': 'c1',
        'title': 'How are we doing?',
        'message_count': 4,
        'last_message_at': '2026-10-09T13:05:00',
      },
      {
        'id': 'c2',
        'title': 'Forecast my sales',
        'message_count': 2,
        'last_message_at': '2026-10-08T09:00:00',
      },
    ])
    ..json('GET /organizations/b1/assistant/conversations/c1', {
      'id': 'c1',
      'title': 'How are we doing?',
      'messages': [
        messageJson('m1', 'user', 'How are we doing?'),
        messageJson('m2', 'assistant', 'You are doing fairly well.'),
      ],
    })
    ..on(
      'DELETE /organizations/b1/assistant/conversations/c2',
      (_) => http.Response('', 204),
    )
    ..json('GET /organizations/b1/assistant/settings', {
      'ai_enabled': true,
      'allow_external_ai': false,
      'allow_model_improvement': false,
      'external_provider_available': false,
      'engine': 'vyterlix-grounded',
      'updated_at': null,
    })
    ..json('PUT /organizations/b1/assistant/settings', {
      'ai_enabled': true,
      'allow_external_ai': false,
      'allow_model_improvement': true,
      'external_provider_available': false,
      'engine': 'vyterlix-grounded',
      'updated_at': '2026-10-09T13:05:00',
    })
    ..json('GET /organizations/b2/assistant/settings', {
      'ai_enabled': true,
      'allow_external_ai': false,
      'allow_model_improvement': false,
      'external_provider_available': true,
      'engine': 'vyterlix-grounded',
      'updated_at': null,
    });
  return server;
}

Future<Harness> openAsk(
  WidgetTester tester,
  FakeServer server, {
  String name = 'Fakeham Bakery',
}) async {
  tester.view.physicalSize = const Size(800, 2000);
  tester.view.devicePixelRatio = 1.0;
  addTearDown(tester.view.reset);
  final h = await pumpApp(
    tester,
    server,
    store: MemorySessionStore()..refreshToken = 'saved',
  );
  await tester.tap(find.text(name));
  await tester.pumpAndSettle();
  await tester.tap(
    find.descendant(
      of: find.byType(NavigationBar),
      matching: find.text('More'),
    ),
  );
  await tester.pumpAndSettle();
  await tester.tap(find.text('Ask Vyterlix'));
  await tester.pumpAndSettle();
  return h;
}

Future<void> ask(WidgetTester tester, String text) async {
  await tester.enterText(
    find.widgetWithText(TextField, 'Ask about your business...'),
    text,
  );
  await tester.tap(find.byTooltip('Ask'));
  await tester.pumpAndSettle();
}

Future<void> menu(WidgetTester tester, String entry) async {
  await tester.tap(find.byType(PopupMenuButton<String>).last);
  await tester.pumpAndSettle();
  await tester.tap(find.text(entry));
  await tester.pumpAndSettle();
}

void main() {
  group('what comes from the server', () {
    test('an answer is read with its sources and what was looked up', () {
      final a = Answer.fromJson(answerJson('How are we doing?'));
      expect(a.conversationId, 'c1');
      expect(a.question.isMine, isTrue);
      expect(a.answer.isMine, isFalse);
      expect(a.answer.sources.map((s) => s.label), [
        'Sales for September 2026',
        'Costs',
      ]);
      expect(a.answer.sources.last.link, isNull);
      expect(a.answer.toolCalls.map((c) => (c.tool, c.ok, c.factCount)), [
        ('kpi_value', true, 2),
        ('forecast', false, 0),
      ]);
      expect(a.answer.engine, 'vyterlix-grounded');
      expect(a.answer.sentOutside, isFalse);
    });

    test('conversations and the choices about AI are read', () {
      final c = Conversation.fromJson({
        'id': 'c',
        'title': 'T',
        'message_count': 6,
        'last_message_at': '2026-10-09T13:05:00',
      });
      expect((c.title, c.messageCount), ('T', 6));
      final s = AiSettings.fromJson({
        'ai_enabled': true,
        'allow_external_ai': true,
        'allow_model_improvement': false,
        'external_provider_available': true,
      });
      expect(
        (
          s.aiEnabled,
          s.allowExternalAi,
          s.allowModelImprovement,
          s.externalProviderAvailable,
        ),
        (true, true, false, true),
      );
      final pending = ChatMessage.pending('Hello');
      expect(
        (pending.isMine, pending.content, pending.id),
        (true, 'Hello', 'pending'),
      );
    });
  });

  group('asking', () {
    testWidgets('opens with what it is, and questions to try', (tester) async {
      await openAsk(tester, askServer());
      expect(
        find.textContaining('Answers come only from your own figures'),
        findsOneWidget,
      );
      expect(find.text('Try asking'), findsOneWidget);
      expect(
        find.widgetWithText(ActionChip, 'How are we doing?'),
        findsOneWidget,
      );
      expect(find.widgetWithText(ActionChip, 'Did it work?'), findsOneWidget);
    });

    testWidgets('a question typed is answered, with where it came from', (
      tester,
    ) async {
      final server = askServer();
      await openAsk(tester, server);
      await ask(tester, '  How are we doing?  ');
      final sent = server.bodyOf(
        server.to('POST /organizations/b1/assistant/ask').single,
      );
      expect(sent, {'message': 'How are we doing?', 'conversation_id': null});
      expect(find.text('How are we doing?'), findsOneWidget);
      expect(
        find.text('Your sales were £8,717.10 in September.'),
        findsOneWidget,
      );
      expect(find.text('That is up 9.1% on August.'), findsOneWidget);
      expect(
        find.text('From: Sales for September 2026, Costs'),
        findsOneWidget,
      );
      expect(
        find.text(
          'Answered by Vyterlix, from your own results. Nothing left Vyterlix.',
        ),
        findsOneWidget,
      );
      expect(find.text('Try asking'), findsNothing);
    });

    testWidgets('a tapped example is asked as it is', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await tester.tap(find.widgetWithText(ActionChip, 'Forecast my sales'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/assistant/ask').single,
        )['message'],
        'Forecast my sales',
      );
      expect(
        find.text('Your sales were £8,717.10 in September.'),
        findsOneWidget,
      );
    });

    testWidgets('a follow up stays in the same conversation', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await ask(tester, 'How are we doing?');
      await ask(tester, 'Why did sales fall?');
      final sent = server
          .to('POST /organizations/b1/assistant/ask')
          .map(server.bodyOf)
          .toList();
      expect(sent.map((b) => b['conversation_id']), [null, 'c1']);
      expect(find.text('Why did sales fall?'), findsOneWidget);
    });

    testWidgets('what was looked up can be opened', (tester) async {
      await openAsk(tester, askServer());
      await ask(tester, 'How are we doing?');
      expect(find.text('kpi_value: 2 fact(s) found'), findsNothing);
      await tester.tap(find.text('What was looked up'));
      await tester.pumpAndSettle();
      expect(find.text('kpi_value: 2 fact(s) found'), findsOneWidget);
      expect(find.text('forecast: nothing found'), findsOneWidget);
    });

    testWidgets('an answer that used an outside provider says so', (
      tester,
    ) async {
      final server = askServer()
        ..on(
          'POST /organizations/b1/assistant/ask',
          (r) => jsonResponse(answerJson('x', outside: true)),
        );
      await openAsk(tester, server);
      await ask(tester, 'x');
      expect(
        find.text(
          'Answered by Vyterlix, from your own results. The facts behind this answer were sent to an outside AI provider, as you allowed.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('an empty question is not sent', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await ask(tester, '   ');
      expect(server.to('POST /organizations/b1/assistant/ask'), isEmpty);
    });

    testWidgets('a refusal is shown and what was typed is kept', (
      tester,
    ) async {
      final server = askServer()
        ..on(
          'POST /organizations/b1/assistant/ask',
          (_) => errorResponse(
            403,
            'ai_disabled',
            'The assistant is switched off for this business.',
          ),
        );
      await openAsk(tester, server);
      await ask(tester, 'How are we doing?');
      expect(
        find.text('The assistant is switched off for this business.'),
        findsOneWidget,
      );
      expect(
        tester
            .widget<TextField>(
              find.widgetWithText(TextField, 'Ask about your business...'),
            )
            .controller!
            .text,
        'How are we doing?',
      );
      expect(
        find.text('Try asking'),
        findsOneWidget,
      ); // nothing was added to the conversation
    });

    testWidgets('a plan that does not include the assistant says so', (
      tester,
    ) async {
      final server = askServer()
        ..on(
          'POST /organizations/b1/assistant/ask',
          (_) => errorResponse(
            402,
            'plan_limit',
            'The AI assistant is not part of the Starter plan. Upgrade to use it.',
          ),
        );
      await openAsk(tester, server);
      await ask(tester, 'How are we doing?');
      expect(
        find.text(
          'The AI assistant is not part of the Starter plan. Upgrade to use it.',
        ),
        findsOneWidget,
      );
    });

    testWidgets('a new conversation starts again', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await ask(tester, 'How are we doing?');
      await tester.tap(find.byTooltip('New conversation'));
      await tester.pumpAndSettle();
      expect(find.text('Try asking'), findsOneWidget);
      await ask(tester, 'Forecast my sales');
      expect(
        server
            .to('POST /organizations/b1/assistant/ask')
            .map(server.bodyOf)
            .map((b) => b['conversation_id']),
        [null, null],
      );
    });
  });

  group('earlier conversations', () {
    testWidgets('are listed and one can be carried on', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await menu(tester, 'Earlier conversations');
      expect(find.text('How are we doing?'), findsOneWidget);
      expect(find.text('2 question(s), 09/10/2026, 13:05'), findsOneWidget);
      expect(find.text('1 question(s), 08/10/2026, 09:00'), findsOneWidget);
      await tester.tap(find.text('How are we doing?'));
      await tester.pumpAndSettle();
      expect(find.text('You are doing fairly well.'), findsOneWidget);
      await ask(tester, 'And last month?');
      expect(
        server.bodyOf(
          server.to('POST /organizations/b1/assistant/ask').single,
        )['conversation_id'],
        'c1',
      );
    });

    testWidgets('one can be deleted after being asked', (tester) async {
      final server = askServer();
      await openAsk(tester, server);
      await menu(tester, 'Earlier conversations');
      await tester.tap(find.byTooltip('Delete').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Keep it'));
      await tester.pumpAndSettle();
      expect(
        server.to('DELETE /organizations/b1/assistant/conversations/c2'),
        isEmpty,
      );
      await tester.tap(find.byTooltip('Delete').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete it'));
      await tester.pumpAndSettle();
      expect(
        server.to('DELETE /organizations/b1/assistant/conversations/c2'),
        hasLength(1),
      );
      expect(
        server.to('GET /organizations/b1/assistant/conversations'),
        hasLength(2),
      );
    });

    testWidgets('none yet is said so', (tester) async {
      final server = askServer()
        ..json('GET /organizations/b1/assistant/conversations', []);
      await openAsk(tester, server);
      await menu(tester, 'Earlier conversations');
      expect(find.text('None yet.'), findsOneWidget);
    });

    testWidgets('a problem loading can be tried again', (tester) async {
      final server = askServer()
        ..on(
          'GET /organizations/b1/assistant/conversations',
          (_) => errorResponse(
            500,
            'internal_error',
            'An unexpected error occurred',
          ),
        );
      await openAsk(tester, server);
      await menu(tester, 'Earlier conversations');
      expect(find.text('An unexpected error occurred'), findsOneWidget);
      server.json('GET /organizations/b1/assistant/conversations', []);
      await tester.tap(find.text('Try again'));
      await tester.pumpAndSettle();
      expect(find.text('None yet.'), findsOneWidget);
    });
  });

  group('AI and your data', () {
    testWidgets('an owner can change the choices and save them', (
      tester,
    ) async {
      final server = askServer();
      await openAsk(tester, server);
      await menu(tester, 'AI and your data');
      expect(
        find.textContaining('No outside AI provider is set up'),
        findsOneWidget,
      );
      await tester.tap(
        find.text('Allow my conversations to be used to improve AI models'),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      expect(
        server.bodyOf(
          server.to('PUT /organizations/b1/assistant/settings').single,
        ),
        {
          'ai_enabled': true,
          'allow_external_ai': false,
          'allow_model_improvement': true,
        },
      );
      expect(find.text('Saved'), findsOneWidget);
    });

    testWidgets('outside AI cannot be switched on when none is set up', (
      tester,
    ) async {
      await openAsk(tester, askServer());
      await menu(tester, 'AI and your data');
      final outside = tester.widget<SwitchListTile>(
        find.widgetWithText(
          SwitchListTile,
          'Allow an outside AI provider to reword answers',
        ),
      );
      expect(outside.onChanged, isNull);
    });

    testWidgets('a viewer can read the choices but not change them', (
      tester,
    ) async {
      await openAsk(tester, askServer(), name: 'Second Shop');
      await menu(tester, 'AI and your data');
      expect(
        find.textContaining('By default nothing leaves Vyterlix'),
        findsOneWidget,
      );
      expect(find.text('Only the owner can change these.'), findsOneWidget);
      expect(find.text('Save'), findsNothing);
      for (final s in tester.widgetList<SwitchListTile>(
        find.byType(SwitchListTile),
      )) {
        expect(s.onChanged, isNull);
      }
    });

    testWidgets('a refusal when saving is shown', (tester) async {
      final server = askServer()
        ..on(
          'PUT /organizations/b1/assistant/settings',
          (_) =>
              errorResponse(403, 'forbidden', 'Your role does not allow this'),
        );
      await openAsk(tester, server);
      await menu(tester, 'AI and your data');
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      expect(find.text('Your role does not allow this'), findsOneWidget);
    });
  });
}
