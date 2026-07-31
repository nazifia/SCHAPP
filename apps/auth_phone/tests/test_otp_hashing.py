"""Code generation and hashing, with no database in the way."""

import re
from collections import Counter

from apps.auth_phone.services import CODE_LENGTH, generate_code, hash_code


def test_code_is_always_six_digits_including_leading_zeros():
    codes = [generate_code() for _ in range(500)]
    assert all(re.fullmatch(r"\d{6}", c) for c in codes)
    assert all(len(c) == CODE_LENGTH for c in codes)


def test_codes_are_not_obviously_biased():
    # Crude sanity check on the CSPRNG: 500 draws from 10^6 should not repeat
    # much, and should not all start with the same digit.
    codes = [generate_code() for _ in range(500)]
    assert len(set(codes)) > 490
    first_digits = Counter(c[0] for c in codes)
    assert len(first_digits) >= 8


def test_hash_is_bound_to_the_request_id():
    # The same code issued for two requests must not verify interchangeably.
    assert hash_code("req-a", "123456") != hash_code("req-b", "123456")


def test_hash_is_stable_and_hides_the_code():
    digest = hash_code("req-a", "123456")
    assert digest == hash_code("req-a", "123456")
    assert "123456" not in digest
    assert len(digest) == 64


def test_wrong_code_produces_a_different_hash():
    assert hash_code("req-a", "123456") != hash_code("req-a", "123457")
