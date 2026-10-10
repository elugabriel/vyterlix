// An optional check against a real, running Vyterlix server (skipped unless asked for):
//
//   flutter test test/live_smoke_test.dart \
//     --dart-define=LIVE_API_BASE_URL=http://localhost:8000/api/v1 \
//     --dart-define=LIVE_REFRESH_TOKEN=<a refresh token made for a mobile session>
//
// It proves the app and the server agree on the shape of the sign-in, the list of businesses and
// the Today screen, which the other tests (against a pretend server) cannot.

import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:vyterlix_mobile/api/api_client.dart';
import 'package:vyterlix_mobile/auth/auth_controller.dart';
import 'package:vyterlix_mobile/auth/session_store.dart';
import 'package:vyterlix_mobile/models.dart';
import 'package:vyterlix_mobile/models_actions.dart';

const _base = String.fromEnvironment('LIVE_API_BASE_URL');
const _token = String.fromEnvironment('LIVE_REFRESH_TOKEN');

void main() {
  final skip = (_base.isEmpty || _token.isEmpty)
      ? 'needs LIVE_API_BASE_URL and LIVE_REFRESH_TOKEN'
      : null;

  test('a phone can use a real server end to end', () async {
    HttpOverrides.global = null; // allow real connections in a test
    final api = ApiClient(baseUrl: _base);
    final store = MemorySessionStore()..refreshToken = _token;
    final auth = AuthController(
      api: api,
      store: store,
      deviceName: 'Smoke test',
    );

    await auth.start();
    expect(auth.status, AuthStatus.signedIn);
    expect(
      store.refreshToken,
      isNot(_token),
      reason: 'the refresh token is replaced each time',
    );
    expect(auth.user!.email, contains('@'));

    final businesses = [
      for (final item in await api.get('/organizations') as List)
        Business.fromJson(item as Map<String, dynamic>),
    ];
    expect(businesses, isNotEmpty);

    final dashboard = Dashboard.fromJson(
      await api.get('/organizations/${businesses.first.id}/dashboard')
          as Map<String, dynamic>,
    );
    expect(dashboard.headline, isNotEmpty);

    final base = '/organizations/${businesses.first.id}';
    final alerts = [
      for (final item
          in await api.get('$base/alerts', query: {'status': 'open'}) as List)
        AlertSummary.fromJson(item as Map<String, dynamic>),
    ];
    final counts = AlertCounts.fromJson(
      await api.get('$base/alerts/summary') as Map<String, dynamic>,
    );
    expect(counts.open, alerts.length);
    if (alerts.isNotEmpty) {
      final detail = AlertDetail.fromJson(
        await api.get('$base/alerts/${alerts.first.id}')
            as Map<String, dynamic>,
      );
      expect(detail.events, isNotEmpty);
    }
    final inbox = Inbox.fromJson(
      await api.get('$base/notifications', query: {'limit': '5'})
          as Map<String, dynamic>,
    );
    expect(inbox.unread, greaterThanOrEqualTo(0));

    final counts2 = ActionCounts.fromJson(
      await api.get('$base/actions/summary') as Map<String, dynamic>,
    );
    final actions = [
      for (final item in await api.get(
        '$base/actions',
        query: {'open_only': 'true'},
      ) as List)
        ActionSummary.fromJson(item as Map<String, dynamic>),
    ];
    expect(counts2.open, greaterThanOrEqualTo(0));
    if (actions.isNotEmpty) {
      final detail = ActionDetail.fromJson(
        await api.get('$base/actions/${actions.first.id}')
            as Map<String, dynamic>,
      );
      expect(detail.title, isNotEmpty);
    }
    final ideas = [
      for (final item in await api.get(
        '$base/recommendations',
        query: {'status': 'open'},
      ) as List)
        RecommendationSummary.fromJson(item as Map<String, dynamic>),
    ];
    if (ideas.isNotEmpty) {
      final detail = RecommendationDetail.fromJson(
        await api.get('$base/changes/${ideas.first.eventId}/recommendation')
            as Map<String, dynamic>,
      );
      expect(detail.headline, isNotEmpty);
    }

    await auth.logout();
    expect(auth.status, AuthStatus.signedOut);
    await expectLater(api.get('/organizations'), throwsA(isA<ApiException>()));
  }, skip: skip);
}
