import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:vyterlix_mobile/api/api_client.dart';
import 'package:vyterlix_mobile/auth/auth_controller.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/format.dart';
import 'package:vyterlix_mobile/models.dart';

import 'support.dart';

void main() {
  group('UK formatting', () {
    test('money is in pounds with two decimals and thousands separators', () {
      expect(gbp('8717.1'), '£8,717.10');
      expect(gbp('0'), '£0.00');
      expect(gbp('1234567.891'), '£1,234,567.89');
      expect(gbp('-250.5'), '-£250.50');
    });

    test('a figure is shown in its own unit', () {
      expect(formatValue('8717.1', 'gbp'), '£8,717.10');
      expect(formatValue('75.6', 'percent'), '75.6%');
      expect(formatValue('75', 'percent'), '75.0%');
      expect(formatValue('15864', 'count'), '15,864');
      expect(formatValue('2.5', 'ratio'), '2.50 times');
      expect(formatValue(null, 'gbp'), '–');
    });

    test('months and dates are written the English way', () {
      expect(monthName('2026-09-01'), 'September 2026');
      expect(monthName('2027-01-01'), 'January 2027');
      expect(monthName('2026-12-01'), 'December 2026');
      expect(ukDate('2027-03-31'), '31/03/2027');
      expect(ukDate('2026-10-09T12:00:00+00:00'), '09/10/2026');
    });
  });

  group('what the server sends', () {
    test('a dashboard is read in full', () {
      final d = Dashboard.fromJson(dashboard(setup: true));
      expect(
        d.headline,
        '2 things need your attention today, 1 of them serious.',
      );
      expect(d.attention.map((a) => a.severity), ['high', 'medium']);
      expect(d.attention.first.canAct, isTrue);
      expect(d.moreAttention, 3);
      expect(d.health!.score, 69);
      expect(d.health!.previousScore, 89);
      expect(d.health!.weakest, 'Sales');
      expect(d.figures, hasLength(3));
      expect(d.setup!.done, 3);
      expect(d.unreadNotifications, 6);
    });

    test('a dashboard with nothing in it is read too', () {
      final d = Dashboard.fromJson({
        'role': 'viewer',
        'headline': 'Nothing needs your attention today.',
        'attention': [],
        'health': null,
        'figures': [],
        'setup': null,
        'can_act': false,
      });
      expect(d.attention, isEmpty);
      expect(d.health, isNull);
      expect(d.moreAttention, 0);
      expect(d.unreadNotifications, 0);
      expect(d.canAct, isFalse);
    });

    test('a figure change is good or bad by which way is good', () {
      FigureGlance figure(String? change, String direction) =>
          FigureGlance.fromJson({
            'code': 'x',
            'name': 'X',
            'unit': 'gbp',
            'period': '2026-09-01',
            'value': '1',
            'change_pct': change, 'direction': direction, //
          });
      expect(figure('9.1', 'up_good').isGood, isTrue);
      expect(figure('-9.1', 'up_good').isGood, isFalse);
      expect(figure('9.1', 'down_good').isGood, isFalse);
      expect(figure('-9.1', 'down_good').isGood, isTrue);
      expect(figure('0', 'up_good').isGood, isTrue);
      expect(figure('9.1', 'neutral').isGood, isNull);
      expect(figure(null, 'up_good').isGood, isNull);
      expect(figure('9.1', 'up_good').changePct, 9.1);
    });

    test('a business is read', () {
      final b = Business.fromJson({
        'id': 'b1',
        'name': 'Fakeham Bakery',
        'role': 'owner',
      });
      expect((b.id, b.name, b.role), ('b1', 'Fakeham Bakery', 'owner'));
    });
  });

  group('the server connection', () {
    test('a good answer is read as it comes', () async {
      final server = FakeServer()..json('GET /ping', {'ok': true});
      final api = ApiClient(baseUrl: baseUrl, client: server.client);
      expect(await api.get('/ping'), {'ok': true});
    });

    test('an error is turned into a message in plain words', () async {
      final server = FakeServer()
        ..on(
          'GET /x',
          (_) => errorResponse(
            403,
            'organization_suspended',
            'This organisation is suspended',
          ),
        );
      final api = ApiClient(baseUrl: baseUrl, client: server.client);
      await expectLater(
        api.get('/x'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 403)
              .having((e) => e.code, 'code', 'organization_suspended')
              .having(
                (e) => e.message,
                'message',
                'This organisation is suspended',
              )
              .having((e) => e.isUnreachable, 'unreachable', isFalse),
        ),
      );
    });

    test(
      'an answer that is not the usual shape still gives a kind message',
      () async {
        final server = FakeServer()
          ..on('GET /x', (_) => http.Response('<html>bad gateway</html>', 502));
        final api = ApiClient(baseUrl: baseUrl, client: server.client);
        await expectLater(
          api.get('/x'),
          throwsA(
            isA<ApiException>()
                .having(
                  (e) => e.message,
                  'message',
                  'Something went wrong. Please try again.',
                )
                .having((e) => e.status, 'status', 502),
          ),
        );
      },
    );

    test('no connection is said to be no connection', () async {
      final server = FakeServer()..down = true;
      final api = ApiClient(baseUrl: baseUrl, client: server.client);
      await expectLater(
        api.get('/x'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.isUnreachable, 'unreachable', isTrue)
              .having(
                (e) => e.message,
                'message',
                contains('Could not reach Vyterlix'),
              ),
        ),
      );
    });

    test(
      'the token is sent, and the path and query are put together properly',
      () async {
        final server = FakeServer()
          ..json('GET /things', [])
          ..json('POST /things', {});
        final api = ApiClient(baseUrl: baseUrl, client: server.client)
          ..accessToken = 'abc';
        await api.get('/things', query: {'limit': '5'});
        final request = server.to('GET /things').single;
        expect(request.headers['Authorization'], 'Bearer abc');
        expect(
          request.url.toString(),
          'http://test.local/api/v1/things?limit=5',
        );
        await api.post('/things', body: {'a': 1}, auth: false);
        final post = server.to('POST /things').single;
        expect(post.headers.containsKey('Authorization'), isFalse);
        expect(server.bodyOf(post), {'a': 1});
        expect(post.headers['Content-Type'], 'application/json');
      },
    );

    test(
      'a token that has run out is swapped once and the request tried again',
      () async {
        var calls = 0;
        final server = FakeServer()
          ..on('GET /things', (request) {
            calls++;
            return request.headers['Authorization'] == 'Bearer new'
                ? jsonResponse({'ok': true})
                : errorResponse(
                    401,
                    'token_expired',
                    'Your access token has expired.',
                  );
          });
        final api = ApiClient(baseUrl: baseUrl, client: server.client)
          ..accessToken = 'old';
        var refreshes = 0;
        api.refresher = () async {
          refreshes++;
          api.accessToken = 'new';
          return true;
        };
        expect(await api.get('/things'), {'ok': true});
        expect((calls, refreshes), (2, 1));
      },
    );

    test('if the sign-in cannot be renewed the first answer is what the caller gets', () async {
      final server = FakeServer()
        ..on(
          'GET /things',
          (_) => errorResponse(401, 'session_ended', 'Your session has ended.'),
        );
      final api = ApiClient(baseUrl: baseUrl, client: server.client)
        ..accessToken = 'old';
      api.refresher = () async => false;
      await expectLater(
        api.get('/things'),
        throwsA(
          isA<ApiException>().having((e) => e.code, 'code', 'session_ended'),
        ),
      );
      expect(server.to('GET /things'), hasLength(1));
    });

    test('a request that is still refused after a renewal is not tried a third time', () async {
      final server = FakeServer()
        ..on('GET /things', (_) => errorResponse(401, 'invalid_token', 'No'));
      final api = ApiClient(baseUrl: baseUrl, client: server.client)
        ..accessToken = 'old';
      var refreshes = 0;
      api.refresher = () async {
        refreshes++;
        return true;
      };
      await expectLater(api.get('/things'), throwsA(isA<ApiException>()));
      expect((server.to('GET /things').length, refreshes), (2, 1));
    });

    test(
      'a refused sign-in is not run through the renewal (that would never end)',
      () async {
        final server = FakeServer()
          ..on(
            'POST /auth/login',
            (_) => errorResponse(
              401,
              'invalid_credentials',
              'Incorrect email or password',
            ),
          );
        final api = ApiClient(baseUrl: baseUrl, client: server.client);
        var refreshes = 0;
        api.refresher = () async {
          refreshes++;
          return true;
        };
        await expectLater(
          api.post('/auth/login', auth: false, body: {}),
          throwsA(
            isA<ApiException>().having(
              (e) => e.message,
              'message',
              'Incorrect email or password',
            ),
          ),
        );
        expect(refreshes, 0);
      },
    );
  });

  group('signing in', () {
    test('signing in tells the server this is a phone and keeps the refresh token safe', () async {
      final h = Harness(happyServer());
      await h.auth.login('  jo@example.co.uk ', 'a long password');
      final sent = h.server.bodyOf(h.server.to('POST /auth/login').single);
      expect(sent, {
        'email': 'jo@example.co.uk',
        'password': 'a long password',
        'client': 'mobile',
        'device_name': 'Test phone',
        'app_version': '1.0.0',
      });
      expect(h.auth.status, AuthStatus.signedIn);
      expect(h.auth.user!.fullName, 'Jo Baker');
      expect(h.api.accessToken, 'access-1');
      expect(h.memory.refreshToken, 'refresh-1');
    });

    test('a wrong password says so and keeps nothing', () async {
      final server = happyServer()
        ..on(
          'POST /auth/login',
          (_) => errorResponse(
            401,
            'invalid_credentials',
            'Incorrect email or password',
          ),
        );
      final h = Harness(server);
      await expectLater(
        h.auth.login('jo@example.co.uk', 'wrong'),
        throwsA(
          isA<ApiException>().having(
            (e) => e.message,
            'message',
            'Incorrect email or password',
          ),
        ),
      );
      expect(h.auth.status, AuthStatus.starting);
      expect(h.memory.refreshToken, isNull);
      expect(h.api.accessToken, isNull);
    });

    test('an app that is too old is told to update', () async {
      final server = happyServer()
        ..on(
          'POST /auth/login',
          (_) => errorResponse(
            426,
            'app_update_required',
            'This version of Vyterlix is no longer supported. Please update the app to sign in.',
          ),
        );
      final h = Harness(server);
      await expectLater(
        h.auth.login('jo@example.co.uk', 'x'),
        throwsA(
          isA<ApiException>()
              .having((e) => e.status, 'status', 426)
              .having((e) => e.message, 'message', contains('update')),
        ),
      );
    });

    test(
      'opening the app with nothing saved shows the log in screen',
      () async {
        final h = Harness(happyServer());
        await h.auth.start();
        expect(h.auth.status, AuthStatus.signedOut);
        expect(h.server.requests, isEmpty);
      },
    );

    test('opening the app with a saved sign-in carries on and keeps the new refresh token', () async {
      final h = Harness(
        happyServer(),
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      await h.auth.start();
      expect(h.auth.status, AuthStatus.signedIn);
      expect(h.server.bodyOf(h.server.to('POST /auth/refresh').single), {
        'refresh_token': 'saved',
      });
      expect(h.memory.refreshToken, 'refresh-2');
      expect(h.api.accessToken, 'access-2');
      expect(h.auth.user!.email, 'jo@example.co.uk');
    });

    test('a saved sign-in the server has ended is forgotten', () async {
      final server = happyServer()
        ..on(
          'POST /auth/refresh',
          (_) => errorResponse(
            401,
            'session_ended',
            'Your session has ended. Please log in again.',
          ),
        );
      final h = Harness(
        server,
        store: MemorySessionStore()..refreshToken = 'old',
      );
      await h.auth.start();
      expect(h.auth.status, AuthStatus.signedOut);
      expect(h.memory.refreshToken, isNull);
      expect(h.api.accessToken, isNull);
    });

    test(
      'with no connection the saved sign-in is kept and the person is told',
      () async {
        final server = happyServer()..down = true;
        final h = Harness(
          server,
          store: MemorySessionStore()..refreshToken = 'saved',
        );
        await h.auth.start();
        expect(h.auth.status, AuthStatus.unreachable);
        expect(h.memory.refreshToken, 'saved');
        server.down = false;
        await h.auth.start();
        expect(h.auth.status, AuthStatus.signedIn);
      },
    );

    test(
      'a server that is failing is not taken to mean the sign-in ended',
      () async {
        final server = happyServer()
          ..on(
            'POST /auth/refresh',
            (_) => errorResponse(503, 'service_unavailable', 'Try again soon'),
          );
        final h = Harness(
          server,
          store: MemorySessionStore()..refreshToken = 'saved',
        );
        await h.auth.start();
        expect(h.auth.status, AuthStatus.unreachable);
        expect(h.memory.refreshToken, 'saved');
      },
    );

    test('several requests needing a new token at once make one trip to the server', () async {
      final server = happyServer();
      final h = Harness(
        server,
        store: MemorySessionStore()..refreshToken = 'saved',
      );
      final results = await Future.wait([
        h.auth.refresh(),
        h.auth.refresh(),
        h.auth.refresh(),
      ]);
      expect(results, [true, true, true]);
      expect(server.to('POST /auth/refresh'), hasLength(1));
      expect(h.memory.refreshToken, 'refresh-2');
      await h.auth.refresh(); // a later one is a new trip
      expect(server.to('POST /auth/refresh'), hasLength(2));
    });

    test('a request that finds its token spent renews it through the controller and carries on', () async {
      final server = happyServer();
      var first = true;
      server.on('GET /organizations', (request) {
        if (first) {
          first = false;
          return errorResponse(
            401,
            'token_expired',
            'Your access token has expired.',
          );
        }
        return request.headers['Authorization'] == 'Bearer access-2'
            ? jsonResponse(businessList)
            : errorResponse(401, 'invalid_token', 'No');
      });
      final h = Harness(server);
      await h.auth.login('jo@example.co.uk', 'a long password');
      final list = await h.api.get('/organizations') as List;
      expect(list, hasLength(2));
      expect(h.memory.refreshToken, 'refresh-2');
    });

    test(
      'signing out ends the session on the server and forgets it here',
      () async {
        final h = Harness(happyServer());
        await h.auth.login('jo@example.co.uk', 'a long password');
        await h.auth.logout();
        expect(h.server.bodyOf(h.server.to('POST /auth/logout').single), {
          'refresh_token': 'refresh-1',
        });
        expect(h.auth.status, AuthStatus.signedOut);
        expect(h.memory.refreshToken, isNull);
        expect(h.api.accessToken, isNull);
        expect(h.auth.user, isNull);
      },
    );

    test('signing out works even if the server cannot be reached', () async {
      final h = Harness(happyServer());
      await h.auth.login('jo@example.co.uk', 'a long password');
      h.server.down = true;
      await h.auth.logout();
      expect(h.auth.status, AuthStatus.signedOut);
      expect(h.memory.refreshToken, isNull);
    });

    test(
      'forgetting the password asks for a reset email and passes the answer on',
      () async {
        final h = Harness(happyServer());
        final message = await h.auth.forgotPassword(' jo@example.co.uk ');
        expect(message, contains('we have sent a link'));
        expect(
          h.server.bodyOf(h.server.to('POST /auth/forgot-password').single),
          {'email': 'jo@example.co.uk'},
        );
        expect(
          h.server
              .to('POST /auth/forgot-password')
              .single
              .headers
              .containsKey('Authorization'),
          isFalse,
        );
      },
    );
  });
}
