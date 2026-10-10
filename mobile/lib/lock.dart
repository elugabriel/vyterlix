import 'package:flutter/foundation.dart';
import 'package:local_auth/local_auth.dart';

import 'auth/session_store.dart';

/// Asking the phone to check the person (fingerprint, face or the phone's own PIN). Tests give it a
/// made-up one.
abstract class PhoneLock {
  /// Whether this phone has any way of checking the person.
  Future<bool> available();

  /// True when the person proved who they are.
  Future<bool> check(String reason);
}

class SystemPhoneLock implements PhoneLock {
  const SystemPhoneLock();

  @override
  Future<bool> available() async {
    try {
      return await LocalAuthentication().isDeviceSupported();
    } catch (_) {
      return false;
    }
  }

  @override
  Future<bool> check(String reason) async {
    try {
      return await LocalAuthentication().authenticate(localizedReason: reason);
    } catch (_) {
      return false; // cancelled, locked out or not set up
    }
  }
}

/// Whether the app asks for the phone's fingerprint, face or PIN, and whether it is asking now.
class AppLock extends ChangeNotifier {
  AppLock({
    required this.phone,
    required this.store,
    this.after = const Duration(seconds: 30),
  });

  final PhoneLock phone;
  final SessionStore store;

  /// How long the app can be out of sight before it locks.
  final Duration after;

  bool enabled = false;
  bool locked = false;
  bool supported = false;
  DateTime? _leftAt;

  /// Read the saved choice; if on, the app starts locked.
  Future<void> load() async {
    supported = await phone.available();
    enabled = supported && await store.readLockEnabled();
    locked = enabled;
    notifyListeners();
  }

  /// Turn it on, but only once the person has proved it works on this phone.
  Future<bool> enable() async {
    if (!await phone.available()) return false;
    if (!await phone.check('Turn on unlocking with your phone lock')) {
      return false;
    }
    await store.writeLockEnabled(true);
    enabled = true;
    notifyListeners();
    return true;
  }

  Future<void> disable() async {
    await store.writeLockEnabled(false);
    enabled = false;
    locked = false;
    notifyListeners();
  }

  Future<bool> unlock() async {
    if (!locked) return true;
    if (!await phone.check('Unlock Vyterlix')) return false;
    locked = false;
    notifyListeners();
    return true;
  }

  /// The app went out of sight.
  void left([DateTime? now]) => _leftAt = now ?? DateTime.now();

  /// The app came back: lock it if it was away long enough.
  void returned([DateTime? now]) {
    final left = _leftAt;
    _leftAt = null;
    if (enabled &&
        !locked &&
        left != null &&
        (now ?? DateTime.now()).difference(left) >= after) {
      locked = true;
      notifyListeners();
    }
  }
}
