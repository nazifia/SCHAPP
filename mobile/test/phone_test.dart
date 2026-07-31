import 'package:flutter_test/flutter_test.dart';
import 'package:schapp/src/phone.dart';

void main() {
  group('normalizePhone', () {
    // The table from docs/auth.md. Every form here must land on one E.164.
    const forms = [
      '08031234567',
      '8031234567',
      '2348031234567',
      '+234 803 123 4567',
      '+234-803-123-4567',
      '234 0803 123 4567', // double-prefixed, as contact exports produce
      '(0803) 123-4567',
    ];

    for (final raw in forms) {
      test('$raw -> +2348031234567', () {
        expect(normalizePhone(raw), '+2348031234567');
      });
    }

    test('empty is MSISDN_EMPTY', () {
      expect(
        () => normalizePhone('   '),
        throwsA(
          isA<InvalidMsisdn>().having((e) => e.code, 'code', 'MSISDN_EMPTY'),
        ),
      );
    });

    test('letters are MSISDN_NON_NUMERIC', () {
      expect(
        () => normalizePhone('080ABC34567'),
        throwsA(
          isA<InvalidMsisdn>().having(
            (e) => e.code,
            'code',
            'MSISDN_NON_NUMERIC',
          ),
        ),
      );
    });

    test('wrong length is MSISDN_BAD_LENGTH', () {
      expect(
        () => normalizePhone('0803123456'),
        throwsA(
          isA<InvalidMsisdn>().having(
            (e) => e.code,
            'code',
            'MSISDN_BAD_LENGTH',
          ),
        ),
      );
    });
  });

  test('formatPhone reads the way Nigerians write it', () {
    expect(formatPhone('+2348031234567'), '0803 123 4567');
  });

  test('maskPhone keeps the last four only', () {
    expect(maskPhone('+2348031234567'), '+234 803 ••• 4567');
  });

  test('phoneErrorFor is null for a good number', () {
    expect(phoneErrorFor('0803 123 4567'), isNull);
    expect(phoneErrorFor('123'), isNotNull);
  });
}
