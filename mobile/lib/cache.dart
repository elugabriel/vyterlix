import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

/// The last good answer to a request, kept so the app has something to show with no signal.
class SavedResponse {
  const SavedResponse({required this.body, required this.savedAt});

  final String body;
  final DateTime savedAt;
}

abstract class ResponseCache {
  Future<SavedResponse?> read(String key);
  Future<void> write(String key, String body, DateTime at);

  /// Whether anything at all is saved (used to open the app with no signal).
  Future<bool> hasAny();

  /// Forget everything (when the person logs out, so the next person never sees it).
  Future<void> clear();
}

/// For tests: kept in memory only.
class MemoryResponseCache implements ResponseCache {
  final Map<String, SavedResponse> saved = {};

  @override
  Future<SavedResponse?> read(String key) async => saved[key];

  @override
  Future<void> write(String key, String body, DateTime at) async =>
      saved[key] = SavedResponse(body: body, savedAt: at);

  @override
  Future<bool> hasAny() async => saved.isNotEmpty;

  @override
  Future<void> clear() async => saved.clear();
}

/// Saved in the app's private folder on the phone, one small file per request.
class FileResponseCache implements ResponseCache {
  FileResponseCache([Directory? folder]) : _given = folder;

  final Directory? _given;
  Directory? _dir;

  Future<Directory> _folder() async {
    final existing = _dir;
    if (existing != null) return existing;
    final base = _given ?? await getApplicationSupportDirectory();
    final dir = Directory(
      '${base.path}${Platform.pathSeparator}response_cache',
    );
    await dir.create(recursive: true);
    return _dir = dir;
  }

  Future<File> _file(String key) async {
    final dir = await _folder();
    // a hash that is the same every time the app runs (String.hashCode is not promised to be)
    var hash = 0x811c9dc5;
    for (final unit in utf8.encode(key)) {
      hash = ((hash ^ unit) * 0x01000193) & 0xffffffff;
    }
    return File(
      '${dir.path}${Platform.pathSeparator}${hash.toRadixString(16)}-${key.length}.json',
    );
  }

  @override
  Future<SavedResponse?> read(String key) async {
    try {
      final file = await _file(key);
      if (!await file.exists()) return null;
      final data =
          jsonDecode(await file.readAsString()) as Map<String, dynamic>;
      if (data['key'] != key) return null;
      return SavedResponse(
        body: data['body'] as String,
        savedAt: DateTime.parse(data['saved_at'] as String),
      );
    } catch (error) {
      debugPrint('cache read failed: $error');
      return null;
    }
  }

  @override
  Future<void> write(String key, String body, DateTime at) async {
    try {
      final file = await _file(key);
      await file.writeAsString(
        jsonEncode({
          'key': key,
          'body': body,
          'saved_at': at.toIso8601String(),
        }),
        flush: true,
      );
    } catch (error) {
      debugPrint('cache write failed: $error');
    }
  }

  @override
  Future<bool> hasAny() async {
    try {
      final dir = await _folder();
      return await dir.list().isEmpty == false;
    } catch (_) {
      return false;
    }
  }

  @override
  Future<void> clear() async {
    try {
      final dir = await _folder();
      if (await dir.exists()) await dir.delete(recursive: true);
      _dir = null;
    } catch (error) {
      debugPrint('cache clear failed: $error');
    }
  }
}

/// Tells the screens when what they show came from the phone's saved copy.
class OfflineState extends ChangeNotifier {
  DateTime? showingSavedFrom;

  void showSaved(DateTime savedAt) {
    // keep the oldest, since that is how out of date the screen may be
    final current = showingSavedFrom;
    if (current == null || savedAt.isBefore(current)) {
      showingSavedFrom = savedAt;
      notifyListeners();
    }
  }

  void backOnline() {
    if (showingSavedFrom != null) {
      showingSavedFrom = null;
      notifyListeners();
    }
  }
}
