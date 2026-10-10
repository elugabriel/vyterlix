/// Settings that differ between a developer's machine and the real service.
///
/// Run against another server with
/// `flutter run --dart-define=API_BASE_URL=http://10.0.2.2:8000/api/v1` (10.0.2.2 is how an
/// Android emulator reaches the computer it runs on).
class AppConfig {
  static const apiBaseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  /// The version this build of the app says it is. Keep it equal to `version:` in pubspec.yaml.
  static const appVersion = '1.0.0';
}
