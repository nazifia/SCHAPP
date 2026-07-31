"""Nigerian MSISDN normalisation.

Every phone number entering the system goes through `normalize()` exactly
once, at the trust boundary, and is stored only as E.164. Two layers:

1. **libphonenumber** (`phonenumbers`) decides whether the shape is a possible
   number at all.
2. **The NCC allocation table** decides whether the prefix is actually
   assigned and usable for a personal account.

Order matters. We check *possibility*, not libphonenumber's `is_valid_number`:
its bundled prefix metadata lags NCC assignments by months, and a school
should not be locked out of the platform because a bundled data file is old.
The NCC table is the authority on which prefixes exist.
"""

import re

import phonenumbers

COUNTRY_CODE = "234"
E164_PREFIX = "+" + COUNTRY_CODE
DEFAULT_NSN_LENGTH = 10

_SEPARATORS = re.compile(r"[\s\-().]")


class InvalidMsisdn(ValueError):
    """Carries a machine code so the API and the Flutter client can branch."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _strip_separators(raw: str) -> tuple[str, bool]:
    text = _SEPARATORS.sub("", str(raw))
    has_plus = text.startswith("+")
    if has_plus:
        text = text[1:]
    return text, has_plus


def to_nsn(raw: str) -> str:
    """Reduce any Nigerian dialling form to the 10-digit national number.

    Handles the double-prefixed `234 0803 ...` that phones produce when a
    contact saved locally is exported with a country code bolted on: after
    stripping 234 the remainder still carries the trunk 0.
    """
    if raw is None or not str(raw).strip():
        raise InvalidMsisdn("MSISDN_EMPTY", "Enter a phone number.")

    text, _ = _strip_separators(raw)
    if not text.isdigit():
        raise InvalidMsisdn("MSISDN_NON_NUMERIC", "A phone number may contain digits only.")

    if text.startswith(COUNTRY_CODE):
        rest = text[len(COUNTRY_CODE) :]
        if rest.startswith("0"):
            rest = rest[1:]
    elif text.startswith("0"):
        rest = text[1:]
    else:
        rest = text

    if len(rest) != DEFAULT_NSN_LENGTH:
        raise InvalidMsisdn(
            "MSISDN_BAD_LENGTH",
            "A Nigerian mobile number has 11 digits, e.g. 0803 123 4567.",
        )
    return rest


def normalize(raw: str, default_region: str = "NG") -> str:
    """Return canonical E.164, or raise `InvalidMsisdn`.

    `default_region` exists for completeness; anything other than NG skips the
    NCC layer, since that table only describes the Nigerian plan.
    """
    if default_region != "NG":
        return _normalize_foreign(raw, default_region)

    nsn = to_nsn(raw)
    e164 = E164_PREFIX + nsn

    try:
        parsed = phonenumbers.parse(e164, "NG")
    except phonenumbers.NumberParseException as exc:
        raise InvalidMsisdn("MSISDN_UNPARSEABLE", "That is not a valid phone number.") from exc
    if not phonenumbers.is_possible_number(parsed):
        raise InvalidMsisdn("MSISDN_BAD_LENGTH", "That is not a valid Nigerian phone number.")

    _assert_allocated(nsn)
    return e164


def _normalize_foreign(raw: str, region: str) -> str:
    try:
        parsed = phonenumbers.parse(str(raw), region)
    except phonenumbers.NumberParseException as exc:
        raise InvalidMsisdn("MSISDN_UNPARSEABLE", "That is not a valid phone number.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidMsisdn("MSISDN_INVALID", "That is not a valid phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# NCC allocation layer
# ---------------------------------------------------------------------------
def candidate_ndcs(nsn: str) -> list[str]:
    """NDCs to try, longest first. The NCC publishes both 4- and 5-digit NDCs."""
    return ["0" + nsn[:4], "0" + nsn[:3]]


def lookup_allocation(nsn: str):
    """Longest-prefix match against the allocation table, or None."""
    from .selectors import allocation_map

    table = allocation_map()
    for ndc in candidate_ndcs(nsn):
        if ndc in table:
            return table[ndc]
    return None


def _assert_allocated(nsn: str) -> None:
    allocation = lookup_allocation(nsn)
    if allocation is None:
        raise InvalidMsisdn(
            "MSISDN_UNALLOCATED_PREFIX",
            "That number prefix is not a recognised Nigerian mobile prefix.",
        )
    if allocation["status"] != "ASSIGNED":
        raise InvalidMsisdn(
            "MSISDN_UNALLOCATED_PREFIX",
            "That number prefix is not currently in service.",
        )
    if not allocation["allows_user_accounts"]:
        raise InvalidMsisdn(
            "MSISDN_NOT_PERSONAL",
            "Use a personal mobile number. Service and short-code numbers cannot sign in.",
        )
    if len(nsn) != allocation["nsn_length"]:
        raise InvalidMsisdn(
            "MSISDN_BAD_LENGTH",
            "That number has the wrong number of digits for its prefix.",
        )


def operator_hint(e164: str) -> str | None:
    """Best-guess network. **Never** use this for validation or billing.

    Nigeria has had Mobile Number Portability since 2013, so 0803 no longer
    means MTN. This is good enough for routing-cost estimates and analytics;
    real network resolution belongs to the SMS gateway at send time.
    """
    try:
        nsn = to_nsn(e164)
    except InvalidMsisdn:
        return None
    allocation = lookup_allocation(nsn)
    return allocation["operator"] if allocation else None


def format_display(e164: str) -> str:
    """`+2348031234567` -> `0803 123 4567`, the form Nigerians actually read."""
    try:
        parsed = phonenumbers.parse(e164, "NG")
    except phonenumbers.NumberParseException:
        return e164
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)


def mask(e164: str) -> str:
    """`+2348031234567` -> `+234 803 ••• 4567`. For OTP screens and receipts."""
    if not e164 or len(e164) < 11:
        return e164
    return f"{e164[:4]} {e164[4:7]} ••• {e164[-4:]}"
