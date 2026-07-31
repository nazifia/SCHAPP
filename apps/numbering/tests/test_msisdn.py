"""Table-driven MSISDN tests.

The canonical-form table is the product acceptance criterion: every dialling
variant a Nigerian will actually type must land on one E.164 string.
"""

import pytest

from apps.numbering.msisdn import (
    InvalidMsisdn,
    format_display,
    mask,
    normalize,
    operator_hint,
    to_nsn,
)

pytestmark = pytest.mark.usefixtures("ncc_table")

CANONICAL_CASES = [
    ("08031234567", "+2348031234567"),
    ("8031234567", "+2348031234567"),
    ("2348031234567", "+2348031234567"),
    ("+234 803 123 4567", "+2348031234567"),
    ("+234-803-123-4567", "+2348031234567"),
    ("234 0803 123 4567", "+2348031234567"),  # double-prefixed
    ("09131234567", "+2349131234567"),
]


@pytest.mark.parametrize(("raw", "expected"), CANONICAL_CASES)
def test_every_variant_normalises_to_the_same_e164(raw, expected):
    assert normalize(raw) == expected


def test_the_first_six_variants_are_literally_the_same_number():
    results = {normalize(raw) for raw, _ in CANONICAL_CASES[:6]}
    assert results == {"+2348031234567"}


@pytest.mark.parametrize(
    "raw",
    [
        "  08031234567  ",
        "0803.123.4567",
        "(0803) 123-4567",
        "+2348031234567",
        "0 8 0 3 1 2 3 4 5 6 7",
    ],
)
def test_separators_and_padding_are_ignored(raw):
    assert normalize(raw) == "+2348031234567"


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("", "MSISDN_EMPTY"),
        ("   ", "MSISDN_EMPTY"),
        (None, "MSISDN_EMPTY"),
        ("0803123456", "MSISDN_BAD_LENGTH"),  # one short
        ("080312345678", "MSISDN_BAD_LENGTH"),  # one long
        ("not-a-number", "MSISDN_NON_NUMERIC"),
        ("0803ABC4567", "MSISDN_NON_NUMERIC"),
        ("01234567890", "MSISDN_UNALLOCATED_PREFIX"),  # Lagos landline, not mobile
        ("09141234567", "MSISDN_UNALLOCATED_PREFIX"),  # 0914 is not in the NCC table
        ("07091234567", "MSISDN_UNALLOCATED_PREFIX"),  # WITHDRAWN by the NCC
        ("08191234567", "MSISDN_UNALLOCATED_PREFIX"),  # WITHDRAWN (former Visafone)
        # 0900 is RESERVED, so "not in service" is the truthful answer and it
        # is caught before the not-a-personal-line check.
        ("09001234567", "MSISDN_UNALLOCATED_PREFIX"),
        ("07001234567", "MSISDN_NOT_PERSONAL"),  # VAS / SNS shared block
        ("08001234567", "MSISDN_NOT_PERSONAL"),  # VAS / SNS shared block
    ],
)
def test_invalid_inputs_are_rejected_with_a_machine_code(raw, code):
    with pytest.raises(InvalidMsisdn) as exc:
        normalize(raw)
    assert exc.value.code == code


def test_error_messages_are_human_and_actionable():
    with pytest.raises(InvalidMsisdn) as exc:
        normalize("0803123456")
    assert "11 digits" in exc.value.message


# ---------------------------------------------------------------------------
# Data-driven, not code-driven
# ---------------------------------------------------------------------------
def test_a_new_ncc_block_is_supported_by_data_alone(ncc_table):
    """Acceptance criterion: a new prefix needs no code change, no deploy."""
    unknown = "09201234567"
    with pytest.raises(InvalidMsisdn):
        normalize(unknown)

    # Exactly what `sync_ncc_allocations` would write — nothing else changes.
    ncc_table["0920"] = {
        "ndc": "0920",
        "operator": "MTN",
        "nsn_length": 10,
        "status": "ASSIGNED",
        "allows_user_accounts": True,
    }

    assert normalize(unknown) == "+2349201234567"


def test_withdrawing_a_block_stops_new_logins(ncc_table):
    ncc_table["0803"]["status"] = "WITHDRAWN"
    with pytest.raises(InvalidMsisdn) as exc:
        normalize("08031234567")
    assert exc.value.code == "MSISDN_UNALLOCATED_PREFIX"


def test_longest_prefix_wins_for_five_digit_ndcs(ncc_table):
    ncc_table["07025"] = {
        "ndc": "07025",
        "operator": "SMILE",
        "nsn_length": 10,
        "status": "ASSIGNED",
        "allows_user_accounts": False,
    }
    # 0702 is assigned and personal; 07025 is not. The longer match must win.
    assert normalize("07021234567") == "+2347021234567"
    with pytest.raises(InvalidMsisdn) as exc:
        normalize("07025123456")
    assert exc.value.code == "MSISDN_NOT_PERSONAL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def test_operator_is_a_hint_only():
    # Correct as an allocation fact; meaningless as a "current network" claim,
    # because MNP has been live in Nigeria since 2013.
    assert operator_hint("+2348031234567") == "MTN"
    assert operator_hint("+2348051234567") == "GLO"
    assert operator_hint("not a number") is None


def test_display_format_is_the_local_dialling_form():
    assert format_display("+2348031234567") == "0803 123 4567"


def test_mask_hides_the_middle():
    assert mask("+2348031234567") == "+234 803 ••• 4567"


def test_to_nsn_is_reusable_on_its_own():
    assert to_nsn("+234 803 123 4567") == "8031234567"
    assert to_nsn("08031234567") == "8031234567"
