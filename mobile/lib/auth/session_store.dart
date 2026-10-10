import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Where the phone keeps what must survive closing the app: the refresh token (a long-lived key
/// to the account, so it lives in the iOS Keychain or the Android Keystore, never in plain files)
/// and which business was open last.
abstract class SessionStore {
  Future<String?> readRefreshToken();
  Future<void> writeRefreshToken(String token);
  Future<String?> readLastBusiness();
  Future<void> writeLastBusiness(String? id);

  /// Forget the sign-in (the choice of business is kept for next time on this phone).
  Future<void> clearSignIn();
}

class SecureSessionStore implements SessionStore {
  SecureSessionStore([FlutterSecureStorage? storage])
    : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  static const _refresh = 'vyterlix.refresh_token';
  static const _business = 'vyterlix.last_business';

  @override
  Future<String?> readRefreshToken() => _storage.read(key: _refresh);

  @override
  Future<void> writeRefreshToken(String token) =>
      _storage.write(key: _refresh, value: token);

  @override
  Future<String?> readLastBusiness() => _storage.read(key: _business);

  @override
  Future<void> writeLastBusiness(String? id) => id == null
      ? _storage.delete(key: _business)
      : _storage.write(key: _business, value: id);

  @override
  Future<void> clearSignIn() => _storage.delete(key: _refresh);
}

/// For tests: nothing is written anywhere.
class MemorySessionStore implements SessionStore {
  String? refreshToken;
  String? lastBusiness;

  @override
  Future<String?> readRefreshToken() async => refreshToken;

  @override
  Future<void> writeRefreshToken(String token) async => refreshToken = token;

  @override
  Future<String?> readLastBusiness() async => lastBusiness;

  @override
  Future<void> writeLastBusiness(String? id) async => lastBusiness = id;

  @override
  Future<void> clearSignIn() async => refreshToken = null;
}
