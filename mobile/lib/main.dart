import 'dart:io' show Platform;

import 'package:flutter/material.dart';

import 'api/api_client.dart';
import 'app.dart';
import 'auth/auth_controller.dart';
import 'auth/session_store.dart';
import 'config.dart';

/// What this phone is called in the list of devices signed in to the account.
String _deviceName() {
  final system = switch (Platform.operatingSystem) {
    'ios' => 'iPhone or iPad',
    'android' => 'Android phone',
    'windows' => 'Windows computer',
    final other => other,
  };
  return 'Vyterlix app on $system';
}

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  final store = SecureSessionStore();
  final api = ApiClient(baseUrl: AppConfig.apiBaseUrl);
  final auth = AuthController(
    api: api,
    store: store,
    deviceName: _deviceName(),
  );
  runApp(VyterlixApp(auth: auth, api: api, store: store));
  auth.start();
}
