import 'dart:async';

import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../config.dart';
import 'session_store.dart';

enum AuthStatus {
  /// Looking for a saved sign-in.
  starting,

  /// Nobody is signed in.
  signedOut,

  /// Signed in.
  signedIn,

  /// There is a saved sign-in but the server could not be reached to use it.
  unreachable,
}

class SignedInUser {
  const SignedInUser({
    required this.id,
    required this.email,
    required this.fullName,
  });

  factory SignedInUser.fromJson(Map<String, dynamic> json) => SignedInUser(
    id: '${json['id']}',
    email: '${json['email']}',
    fullName: '${json['full_name']}',
  );

  final String id;
  final String email;
  final String fullName;
}

/// Who is signed in, and keeping that sign-in alive.
///
/// The server gives a phone a refresh token when it signs in. It is kept in secure storage, and
/// swapped for a new access token (and a new refresh token) whenever one is needed.
class AuthController extends ChangeNotifier {
  AuthController({
    required this.api,
    required this.store,
    this.deviceName = 'Vyterlix app',
    this.appVersion = AppConfig.appVersion,
  }) {
    api.refresher = refresh;
  }

  final ApiClient api;
  final SessionStore store;
  final String deviceName;
  final String appVersion;

  AuthStatus status = AuthStatus.starting;
  SignedInUser? user;

  Future<bool>? _refreshing;

  void _set(AuthStatus next) {
    status = next;
    notifyListeners();
  }

  /// Open the app: pick up a saved sign-in if there is one.
  Future<void> start() async {
    final saved = await store.readRefreshToken();
    if (saved == null) {
      _set(AuthStatus.signedOut);
      return;
    }
    try {
      final ok = await refresh();
      _set(ok ? AuthStatus.signedIn : AuthStatus.signedOut);
    } on ApiException {
      _set(AuthStatus.unreachable);
    }
  }

  /// Sign in with an email and password. Throws [ApiException] with a message to show.
  Future<void> login(String email, String password) async {
    final data = await api.post(
      '/auth/login',
      auth: false,
      body: {
        'email': email.trim(),
        'password': password,
        'client': 'mobile',
        'device_name': deviceName,
        'app_version': appVersion,
      },
    ) as Map<String, dynamic>;
    await _accept(data);
    _set(AuthStatus.signedIn);
  }

  Future<void> _accept(Map<String, dynamic> data) async {
    api.accessToken = data['access_token'] as String;
    final refreshToken = data['refresh_token'] as String?;
    if (refreshToken != null) await store.writeRefreshToken(refreshToken);
    final who = data['user'];
    if (who is Map<String, dynamic>) user = SignedInUser.fromJson(who);
  }

  /// Swap the saved refresh token for fresh tokens. Several callers at once share one trip to the
  /// server (a refresh token only works once). Returns false if the sign-in has ended; throws
  /// [ApiException] if the server could not be reached.
  Future<bool> refresh() {
    final running = _refreshing;
    if (running != null) return running;
    final fresh = _doRefresh().whenComplete(() => _refreshing = null);
    _refreshing = fresh;
    return fresh;
  }

  Future<bool> _doRefresh() async {
    final saved = await store.readRefreshToken();
    if (saved == null) return false;
    try {
      final data = await api.post(
        '/auth/refresh',
        auth: false,
        body: {'refresh_token': saved},
      ) as Map<String, dynamic>;
      await _accept(data);
      return true;
    } on ApiException catch (error) {
      if (error.isUnreachable || error.status >= 500) {
        rethrow; // the server is down, not saying no
      }
      // The server has ended this sign-in (signed out elsewhere, password changed...).
      await _endSignIn();
      return false;
    }
  }

  Future<void> _endSignIn() async {
    api.accessToken = null;
    user = null;
    await store.clearSignIn();
    if (status != AuthStatus.signedOut) _set(AuthStatus.signedOut);
  }

  /// Sign out on this phone and tell the server to end the session.
  Future<void> logout() async {
    final saved = await store.readRefreshToken();
    if (saved != null) {
      try {
        await api.post(
          '/auth/logout',
          auth: false,
          body: {'refresh_token': saved},
        );
      } on ApiException {
        // Signing out on the phone matters more than telling the server.
      }
    }
    await _endSignIn();
  }

  /// Ask for a password-reset email. The server answers the same whether or not the address exists.
  Future<String> forgotPassword(String email) async {
    final data = await api.post(
      '/auth/forgot-password',
      auth: false,
      body: {'email': email.trim()},
    ) as Map<String, dynamic>;
    return '${data['message']}';
  }
}
