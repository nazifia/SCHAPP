"""Flutterwave (v3) adapter.

Differences from Paystack that matter: amounts are in **naira**, our reference
is called `tx_ref`, a successful charge reads `successful` rather than
`success`, and the webhook carries no signature — only a shared secret in the
`verif-hash` header. That last one is weaker, so `parse_webhook` still reports
the amount from the payload and `services.confirm_payment` refuses anything
that does not match the invoice.
"""

import hmac
from decimal import Decimal

from .base import Charge, PaymentGateway, Verification

API = "https://api.flutterwave.com/v3"
HASH_HEADER = "verif-hash"


def _amount(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


class FlutterwaveGateway(PaymentGateway):
    name = "flutterwave"

    def initialize(
        self, *, reference: str, amount: Decimal, email: str, callback_url: str = "", **extra
    ) -> Charge:
        body = self._request(
            f"{API}/payments",
            method="POST",
            payload={
                "tx_ref": reference,
                "amount": str(Decimal(amount)),
                "currency": "NGN",
                "redirect_url": callback_url,
                "customer": {"email": email or "fees@schapp.ng"},
                "meta": extra or {},
            },
        )
        link = (body.get("data") or {}).get("link", "")
        return Charge(
            ok=bool(link),
            reference=reference,
            checkout_url=link,
            error=(
                ""
                if link
                else str(body.get("message", "Flutterwave did not open a checkout."))[:200]
            ),
            raw=body,
        )

    def verify(self, reference: str) -> Verification:
        body = self._request(f"{API}/transactions/verify_by_reference?tx_ref={reference}")
        return self._read(body.get("data") or {}, fallback_reference=reference, envelope=body)

    def verify_signature(self, body: bytes, headers) -> bool:
        """Constant-time compare of the shared hash.

        Flutterwave does not sign the body, so this proves the sender knows
        the secret and nothing about the contents — which is why the amount is
        checked against the invoice downstream.
        """
        sent = (headers.get(HASH_HEADER) or "").strip()
        return bool(sent and self.secret_key and hmac.compare_digest(self.secret_key, sent))

    def parse_webhook(self, payload: dict) -> Verification | None:
        if payload.get("event") not in {"charge.completed", "charge.success"}:
            return None
        return self._read(payload.get("data") or {}, envelope=payload)

    def _read(self, data: dict, *, fallback_reference: str = "", envelope: dict) -> Verification:
        status = str(data.get("status", "")).lower()
        return Verification(
            ok=status == "successful",
            reference=data.get("tx_ref", fallback_reference),
            gateway_reference=str(data.get("id", "") or data.get("flw_ref", "")),
            amount=_amount(data.get("amount")),
            status=status,
            error=(
                "" if status == "successful" else str(envelope.get("message", status or "failed"))
            ),
            raw=envelope,
        )
