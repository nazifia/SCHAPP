"""Gateway adapters, without a network.

These are the pure parts — signature checking and payload reading — so they
need no database and no Paystack account. What is worth pinning down is
exactly the set of things that quietly lose or double money: kobo arithmetic,
a forged webhook, and an event that is not a payment.
"""

import hashlib
import hmac
import json
from decimal import Decimal

from apps.finance.gateways.flutterwave import FlutterwaveGateway
from apps.finance.gateways.paystack import PaystackGateway

SECRET = "sk_test_secret"


def _paystack_body(reference="RCT/26/0001", amount=1_000_00, status="success"):
    return json.dumps(
        {
            "event": "charge.success",
            "data": {
                "reference": reference,
                "id": 302961,
                "amount": amount,
                "status": status,
            },
        }
    ).encode()


def test_paystack_reads_kobo_as_naira():
    body = _paystack_body(amount=12_345_67)
    verification = PaystackGateway(secret_key=SECRET).parse_webhook(json.loads(body))
    assert verification.ok
    assert verification.amount == Decimal("12345.67")
    assert verification.reference == "RCT/26/0001"
    assert verification.gateway_reference == "302961"


def test_paystack_accepts_only_its_own_signature():
    gateway = PaystackGateway(secret_key=SECRET)
    body = _paystack_body()
    good = hmac.new(SECRET.encode(), body, hashlib.sha512).hexdigest()

    assert gateway.verify_signature(body, {"x-paystack-signature": good})
    assert not gateway.verify_signature(body, {"x-paystack-signature": "deadbeef"})
    assert not gateway.verify_signature(body, {})
    # A body edited after signing must fail: this is the whole point.
    assert not gateway.verify_signature(
        _paystack_body(amount=99_999_00), {"x-paystack-signature": good}
    )


def test_paystack_ignores_events_that_are_not_a_charge():
    payload = {"event": "transfer.success", "data": {"reference": "X"}}
    assert PaystackGateway(secret_key=SECRET).parse_webhook(payload) is None


def test_paystack_reports_a_failed_charge_as_not_ok():
    body = _paystack_body(status="failed")
    verification = PaystackGateway(secret_key=SECRET).parse_webhook(json.loads(body))
    assert not verification.ok


def test_flutterwave_matches_the_shared_hash_and_reads_naira():
    gateway = FlutterwaveGateway(secret_key="hash-value")
    assert gateway.verify_signature(b"{}", {"verif-hash": "hash-value"})
    assert not gateway.verify_signature(b"{}", {"verif-hash": "other"})
    assert not gateway.verify_signature(b"{}", {})

    verification = gateway.parse_webhook(
        {
            "event": "charge.completed",
            "data": {"tx_ref": "RCT/26/0002", "id": 99, "amount": 45000, "status": "successful"},
        }
    )
    assert verification.ok
    assert verification.amount == Decimal("45000.00")
    assert verification.gateway_reference == "99"


def test_an_unconfigured_gateway_never_accepts_a_webhook():
    """No key means no verdict — never "no signature required"."""
    assert not PaystackGateway().verify_signature(b"{}", {"x-paystack-signature": "x"})
    assert not FlutterwaveGateway().verify_signature(b"{}", {"verif-hash": ""})
