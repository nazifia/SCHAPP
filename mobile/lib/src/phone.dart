/// Nigerian MSISDN handling, mirroring `apps/numbering/msisdn.py`.
///
/// This is the *shape* layer only: separators, country code, trunk zero,
/// length. The NCC allocation check stays on the server — that table is the
/// authority, it changes without a deploy, and a client copy would go stale.
/// So a number that passes here can still come back `MSISDN_UNALLOCATED_PREFIX`
/// or `MSISDN_NOT_PERSONAL`, and the sign-in screen shows that message as-is.
library;

class InvalidMsisdn implements Exception {
  const InvalidMsisdn(this.code, this.message);

  final String code;
  final String message;

  @override
  String toString() => '$code: $message';
}

const _countryCode = '234';
const _e164Prefix = '+$_countryCode';
const _nsnLength = 10;

final _separators = RegExp(r'[\s\-().]');

/// Reduce any Nigerian dialling form to the 10-digit national number.
///
/// Handles the double-prefixed `234 0803 ...` that contact exports produce:
/// after stripping 234 the remainder still carries the trunk 0.
String toNsn(String? raw) {
  if (raw == null || raw.trim().isEmpty) {
    throw const InvalidMsisdn('MSISDN_EMPTY', 'Enter a phone number.');
  }

  var text = raw.replaceAll(_separators, '');
  if (text.startsWith('+')) text = text.substring(1);
  if (!RegExp(r'^\d+$').hasMatch(text)) {
    throw const InvalidMsisdn(
      'MSISDN_NON_NUMERIC',
      'A phone number may contain digits only.',
    );
  }

  String rest;
  if (text.startsWith(_countryCode)) {
    rest = text.substring(_countryCode.length);
    if (rest.startsWith('0')) rest = rest.substring(1);
  } else if (text.startsWith('0')) {
    rest = text.substring(1);
  } else {
    rest = text;
  }

  if (rest.length != _nsnLength) {
    throw const InvalidMsisdn(
      'MSISDN_BAD_LENGTH',
      'A Nigerian mobile number has 11 digits, e.g. 0803 123 4567.',
    );
  }
  return rest;
}

/// Canonical E.164, or throws [InvalidMsisdn].
String normalizePhone(String? raw) => '$_e164Prefix${toNsn(raw)}';

/// `null` instead of throwing — for live validation while the user types.
String? phoneErrorFor(String raw) {
  try {
    normalizePhone(raw);
    return null;
  } on InvalidMsisdn catch (e) {
    return e.message;
  }
}

/// `+2348031234567` -> `0803 123 4567`, the form Nigerians actually read.
String formatPhone(String e164) {
  try {
    final nsn = toNsn(e164);
    return '0${nsn.substring(0, 3)} ${nsn.substring(3, 6)} ${nsn.substring(6)}';
  } on InvalidMsisdn {
    return e164;
  }
}

/// `+2348031234567` -> `+234 803 ••• 4567`. For OTP screens and receipts.
String maskPhone(String e164) {
  if (e164.length < 11) return e164;
  return '${e164.substring(0, 4)} ${e164.substring(4, 7)} ••• '
      '${e164.substring(e164.length - 4)}';
}
