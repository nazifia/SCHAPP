import 'dart:convert';

/// The API's single error envelope:
///
///     {"error": {"code": "...", "message": "...", "details": {...}}}
///
/// The client branches on [code] only. [message] is written for humans by the
/// backend and is safe to show directly — including the MSISDN and OTP
/// messages, which are already phrased for a user.
class ApiError implements Exception {
  const ApiError({
    required this.code,
    required this.message,
    this.details = const {},
    this.status = 0,
    this.retryAfter,
  });

  final String code;
  final String message;
  final Map<String, dynamic> details;
  final int status;
  final Duration? retryAfter;

  /// Not a server answer: the request never landed.
  static const offlineCode = 'OFFLINE';

  factory ApiError.offline() => const ApiError(
    code: offlineCode,
    message: 'No connection. Showing what was saved on this device.',
  );

  factory ApiError.fromBody(int status, String body, {Duration? retryAfter}) {
    try {
      final decoded = jsonDecode(body);
      final error = (decoded as Map<String, dynamic>)['error'];
      if (error is Map<String, dynamic>) {
        return ApiError(
          code: error['code'] as String? ?? 'ERROR',
          message: error['message'] as String? ?? 'Something went wrong.',
          details: (error['details'] as Map<String, dynamic>?) ?? const {},
          status: status,
          retryAfter: retryAfter,
        );
      }
    } catch (_) {
      // Not our envelope — a proxy, a 502 page, an HTML debug traceback.
    }
    return ApiError(
      code: 'ERROR',
      message: 'Something went wrong. Please try again.',
      status: status,
      retryAfter: retryAfter,
    );
  }

  bool get isOffline => code == offlineCode;

  /// The session is gone for good: refreshing will not help, sign in again.
  /// `TOKEN_NOT_VALID` is SimpleJWT's own `token_not_valid`, upcased by the
  /// envelope handler — it comes from the authentication class rather than a
  /// view, so it is not in the backend's hand-written list and is easy to miss.
  bool get requiresSignIn => const {
    'TOKEN_REUSE_DETECTED',
    'TOKEN_INVALID',
    'TOKEN_NOT_VALID',
    'UNAUTHENTICATED',
  }.contains(code);

  /// The tenant, not the credentials, is the problem — send the user back to
  /// the school picker rather than the keypad.
  bool get requiresSchool => const {
    'TENANT_NOT_FOUND',
    'TENANT_SUSPENDED',
    'TENANT_MISMATCH',
  }.contains(code);

  /// `DEPENDENTS_EXIST` (409): what the refused delete would have taken with
  /// it, as `{"subject registrations": 240}`. Empty for every other error.
  ///
  /// The counts are the whole answer to "why not", so a screen shows the list
  /// rather than the message alone — "still in use" without the numbers reads
  /// as a bug to work around.
  Map<String, int> get dependents {
    if (code != 'DEPENDENTS_EXIST') return const {};
    final raw = details['dependents'];
    if (raw is! Map) return const {};
    return {
      for (final entry in raw.entries)
        '${entry.key}': (entry.value as num?)?.toInt() ?? 0,
    };
  }

  /// `VALIDATION_ERROR` puts DRF's field map in `details`. Flattened to one
  /// message per field for form display.
  Map<String, String> get fieldErrors {
    if (code != 'VALIDATION_ERROR') return const {};
    return {
      for (final entry in details.entries)
        entry.key: entry.value is List
            ? (entry.value as List).first.toString()
            : entry.value.toString(),
    };
  }

  @override
  String toString() => 'ApiError($code, $status): $message';
}
