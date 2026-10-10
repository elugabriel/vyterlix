import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

/// Something the server (or the connection) said no to, in words fit to show.
class ApiException implements Exception {
  const ApiException(this.status, this.code, this.message, {this.details});

  /// The connection failed or timed out, so there was no answer at all.
  const ApiException.unreachable()
    : status = 0,
      code = 'network_error',
      message =
          'Could not reach Vyterlix. Check your connection and try again.',
      details = null;

  final int status;
  final String code;
  final String message;
  final dynamic details;

  bool get isUnreachable => status == 0;

  @override
  String toString() => 'ApiException($status, $code, $message)';
}

/// Talks to the Vyterlix server. It keeps the short-lived access token in memory, and when the
/// server says that token has run out it asks [refresher] for a new one and tries once more.
class ApiClient {
  ApiClient({required this.baseUrl, http.Client? client})
    : _http = client ?? http.Client();

  final String baseUrl;
  final http.Client _http;

  String? accessToken;

  /// Set by the sign-in controller: gets a new access token, or says it could not.
  Future<bool> Function()? refresher;

  Uri _uri(String path, Map<String, String>? query) {
    final base = Uri.parse(baseUrl);
    return base.replace(
      path: '${base.path}$path',
      queryParameters: query == null || query.isEmpty ? null : query,
    );
  }

  Future<dynamic> get(String path, {Map<String, String>? query}) =>
      _send('GET', path, query: query);

  Future<dynamic> post(String path, {Object? body, bool auth = true}) =>
      _send('POST', path, body: body, auth: auth);

  Future<dynamic> patch(String path, {Object? body}) =>
      _send('PATCH', path, body: body);

  Future<dynamic> put(String path, {Object? body}) =>
      _send('PUT', path, body: body);

  Future<dynamic> delete(String path) => _send('DELETE', path);

  Future<dynamic> _send(
    String method,
    String path, {
    Object? body,
    Map<String, String>? query,
    bool auth = true,
  }) {
    return _dispatch(() {
      final request = http.Request(method, _uri(path, query))
        ..headers['Accept'] = 'application/json';
      if (body != null) {
        request.headers['Content-Type'] = 'application/json';
        request.body = jsonEncode(body);
      }
      return request;
    }, auth: auth);
  }

  /// Send a file (a spreadsheet to import, say) along with some text fields.
  Future<dynamic> postFile(
    String path, {
    required Map<String, String> fields,
    required String fileField,
    required List<int> bytes,
    required String filename,
  }) {
    return _dispatch(() {
      final request = http.MultipartRequest('POST', _uri(path, null))
        ..headers['Accept'] = 'application/json'
        ..fields.addAll(fields)
        ..files.add(
          http.MultipartFile.fromBytes(fileField, bytes, filename: filename),
        );
      return request;
    });
  }

  /// Make the request (built afresh each time, since a request can only be sent once), and if the
  /// server says the access token has run out, get a new one and try once more.
  Future<dynamic> _dispatch(
    http.BaseRequest Function() build, {
    bool auth = true,
    bool retried = false,
  }) async {
    final request = build();
    if (auth && accessToken != null) {
      request.headers['Authorization'] = 'Bearer $accessToken';
    }

    http.Response response;
    try {
      final streamed = await _http
          .send(request)
          .timeout(const Duration(seconds: 25));
      response = await http.Response.fromStream(streamed);
    } on Exception {
      throw const ApiException.unreachable();
    }

    if (response.statusCode == 401 && auth && !retried && refresher != null) {
      if (await refresher!()) {
        return _dispatch(build, auth: auth, retried: true);
      }
    }
    return _decode(response);
  }

  dynamic _decode(http.Response response) {
    final text = utf8.decode(response.bodyBytes);
    dynamic data;
    if (text.isNotEmpty) {
      try {
        data = jsonDecode(text);
      } on FormatException {
        data = null;
      }
    }
    if (response.statusCode >= 200 && response.statusCode < 300) return data;
    final error = data is Map ? data['error'] : null;
    if (error is Map) {
      throw ApiException(
        response.statusCode,
        '${error['code'] ?? 'error'}',
        '${error['message'] ?? 'Something went wrong. Please try again.'}',
        details: error['details'],
      );
    }
    throw ApiException(
      response.statusCode,
      'error',
      'Something went wrong. Please try again.',
    );
  }
}
