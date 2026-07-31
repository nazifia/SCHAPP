"""Paystack adapter.

Two Paystack facts the rest of the code must never learn: amounts travel in
**kobo**, and the webhook is signed with **HMAC-SHA512 over the raw body**
keyed by the secret key. Both are converted here.
"""

import hashlib
import hmac
from decimal import Decimal

from .base import Charge, PaymentGateway, Verification

API = "https://api.paystack.co"
SIGNATURE_HEADER = "x-paystack-signature"


def _naira(kobo) -> Decimal:
    return (Decimal(str(kobo or 0)) / 100).quantize(Decimal("0.01"))


class PaystackGateway(PaymentGateway):
    name = "paystack"

    def initialize(
        self, *, reference: str, amount: Decimal, email: str, callback_url: str = "", **extra
    ) -> Charge:
        body = self._request(
            f"{API}/transaction/initialize",
            method="POST",
            payload={
                "email": email or "fees@schapp.ng",
                "amount": int(Decimal(amount) * 100),
                "reference": reference,
                "currency": "NGN",
                **({"callback_url": callback_url} if callback_url else {}),
                "metadata": extra or {},
            },
        )
        data = body.get("data") or {}
        url = data.get("authorization_url", "")
        return Charge(
            ok=bool(url),
            reference=data.get("reference", reference),
            checkout_url=url,
            error=(
                "" if url else str(body.get("message", "Paystack did not open a checkout."))[:200]
            ),
            raw=body,
        )

    def verify(self, reference: str) -> Verification:
        body = self._request(f"{API}/transaction/verify/{reference}")
        return self._read(body.get("data") or {}, fallback_reference=reference, envelope=body)

    def verify_signature(self, body: bytes, headers) -> bool:
        signature = (headers.get(SIGNATURE_HEADER) or "").strip()
        if not (signature and self.secret_key):
            return False
        expected = hmac.new(self.secret_key.encode(), body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, payload: dict) -> Verification | None:
        if payload.get("event") != "charge.success":
            # refunds, transfers, subscription events: not a fee payment
            return None
        return self._read(payload.get("data") or {}, envelope=payload)

    def _read(self, data: dict, *, fallback_reference: str = "", envelope: dict) -> Verification:
        status = str(data.get("status", "")).lower()
        return Verification(
            ok=status == "success",
            reference=data.get("reference", fallback_reference),
            gateway_reference=str(data.get("id", "") or data.get("reference", "")),
            amount=_naira(data.get("amount")),
            status=status,
            error="" if status == "success" else str(envelope.get("message", status or "failed")),
            raw=envelope,
        )
