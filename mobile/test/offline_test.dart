import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/api/api_client.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/cache.dart';

import 'support.dart';

void main() {
  group('the saved copy', () {
    test('is used when there is no connection, and says so', () async {
      final server = happyServer();
      final cache = MemoryResponseCache();
      final offline = OfflineState();
      final api = ApiClient(
        baseUrl: baseUrl,
        client: server.client,
        cache: cache,
        offline: offline,
      );
      final live = await api.get('/organizations');
      expect(offline.showingSavedFrom, isNull);
      server.down = true;
      final saved = await api.get('/organizations');
      expect(saved, live);
      expect(offline.showingSavedFrom, isNotNull);
      server.down = false;
      await api.get('/organizations');
      expect(offline.showingSavedFrom, isNull);
    });

    test('is only used for the same request', () async {
      final server = happyServer();
      final api = ApiClient(
        baseUrl: baseUrl,
        client: server.client,
        cache: MemoryResponseCache(),
        offline: OfflineState(),
      );
      await api.get('/organizations', query: {'limit': '5'});
      server.down = true;
      await expectLater(
        api.get('/organizations', query: {'limit': '6'}),
        throwsA(
          isA<ApiException>().having(
            (e) => e.isUnreachable,
            'unreachable',
            true,
          ),
        ),
      );
      await expectLater(
        api.get('/organizations/b1/alerts'),
        throwsA(isA<ApiException>()),
      );
    });

    test('is not kept for sign-in, devices or the admin portal', () async {
      final server = happyServer()
        ..json('GET /me/sessions', [])
        ..json('GET /admin/health', {});
      final cache = MemoryResponseCache();
      final api = ApiClient(
        baseUrl: baseUrl,
        client: server.client,
        cache: cache,
        offline: OfflineState(),
      );
      await api.get('/me/sessions');
      await api.get('/admin/health');
      expect(cache.saved, isEmpty);
    });

    test(
      'a real refusal from the server is never replaced by the saved copy',
      () async {
        final server = happyServer();
        final api = ApiClient(
          baseUrl: baseUrl,
          client: server.client,
          cache: MemoryResponseCache(),
          offline: OfflineState(),
        );
        await api.get('/organizations');
        server.on(
          'GET /organizations',
          (_) => errorResponse(403, 'forbidden', 'No.'),
        );
        await expectLater(
          api.get('/organizations'),
          throwsA(isA<ApiException>().having((e) => e.status, 'status', 403)),
        );
      },
    );

    test('changes are not saved or replayed', () async {
      final server = happyServer()
        ..json('POST /organizations/b1/sales', {'id': 's'}, 201);
      final cache = MemoryResponseCache();
      final api = ApiClient(
        baseUrl: baseUrl,
        client: server.client,
        cache: cache,
        offline: OfflineState(),
      );
      await api.post('/organizations/b1/sales', body: {'a': 1});
      expect(cache.saved, isEmpty);
      server.down = true;
      await expectLater(
        api.post('/organizations/b1/sales', body: {'a': 1}),
        throwsA(isA<ApiException>()),
      );
    });
  });

  group('kept in a folder on the phone', () {
    test('writes, reads, notices and forgets', () async {
      final dir = await Directory.systemTemp.createTemp('vyterlix_cache_test');
      addTearDown(() async {
        if (await dir.exists()) await dir.delete(recursive: true);
      });
      final cache = FileResponseCache(dir);
      expect(await cache.hasAny(), isFalse);
      expect(await cache.read('a'), isNull);
      final at = DateTime(2026, 10, 9, 13, 5);
      await cache.write('http://x/a?b=1', '{"n":1}', at);
      await cache.write('http://x/b', '[]', at);
      final again = FileResponseCache(dir); // a new run of the app
      final a = await again.read('http://x/a?b=1');
      expect(a!.body, '{"n":1}');
      expect(a.savedAt, at);
      expect(await again.read('http://x/zzz'), isNull);
      expect(await again.hasAny(), isTrue);
      await again.clear();
      expect(await again.hasAny(), isFalse);
      expect(await again.read('http://x/b'), isNull);
    });
  });

  group('in the app', () {
    testWidgets('with no signal it opens on the saved businesses and says so', (
      tester,
    ) async {
      final cache = MemoryResponseCache();
      final seed = happyServer();
      final seedApi = ApiClient(
        baseUrl: baseUrl,
        client: seed.client,
        cache: cache,
        offline: OfflineState(),
      );
      await seedApi.get('/organizations');
      final server = happyServer()..down = true;
      await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
        cache: cache,
      );
      expect(find.text('Fakeham Bakery'), findsOneWidget);
      expect(
        find.textContaining('No connection. Showing what was saved on'),
        findsOneWidget,
      );
      expect(
        find.text(
          'Could not reach Vyterlix. Check your connection and try again.',
        ),
        findsNothing,
      );
    });

    testWidgets('with no signal and nothing saved it asks for a connection', (
      tester,
    ) async {
      final server = happyServer()..down = true;
      await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
        cache: MemoryResponseCache(),
      );
      expect(
        find.text(
          'Could not reach Vyterlix. Check your connection and try again.',
        ),
        findsOneWidget,
      );
      expect(find.textContaining('No connection. Showing'), findsNothing);
    });

    testWidgets('the banner goes when the connection is back', (tester) async {
      final cache = MemoryResponseCache();
      final seedApi = ApiClient(
        baseUrl: baseUrl,
        client: happyServer().client,
        cache: cache,
        offline: OfflineState(),
      );
      await seedApi.get('/organizations');
      final server = happyServer()..down = true;
      final h = await pumpApp(
        tester,
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
        cache: cache,
      );
      expect(find.textContaining('No connection.'), findsOneWidget);
      server.on(
        'GET /organizations',
        (r) => r.headers['Authorization'] == null
            ? errorResponse(401, 'unauthorized', 'Sign in again.')
            : jsonResponse(businessList),
      );
      server.down = false;
      await h.api.get(
        '/organizations',
      ); // the first request renews the sign-in on its own
      await tester.pumpAndSettle();
      expect(find.textContaining('No connection.'), findsNothing);
      expect(h.auth.user, isNotNull);
    });

    testWidgets('logging out forgets everything that was saved', (
      tester,
    ) async {
      final cache = MemoryResponseCache();
      final h = await pumpApp(
        tester,
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
        cache: cache,
      );
      expect(cache.saved, isNotEmpty);
      await h.auth.logout();
      await tester.pumpAndSettle();
      expect(cache.saved, isEmpty);
      expect(jsonEncode(cache.saved), '{}');
    });
  });
}
