"""Gateway that settles nothing, for development and tests.

`initialize` hands back a local URL instead of a checkout page and `verify`
confirms whatever it is asked about, so the whole online flow — reference,
webhook, reconciliation — can be exercised without a Paystack account.

Never resolved unless a caller asks for `"manual"` by name.
"""

from decimal import Decimal

from .base import Charge, PaymentGateway, Verification


class ManualGateway(PaymentGateway):
    name = "manual"

    #: reference -> amount, so `verify` can answer with the amount it was given.
    charges: dict[str, Decimal] = {}

    def initialize(
        self, *, reference: str, amount: Decimal, email: str, callback_url: str = "", **extra
    ) -> Charge:
        ManualGateway.charges[reference] = Decimal(amount)
        return Charge(
            ok=True,
            reference=reference,
            checkout_url=f"{callback_url or 'https://example.test/pay'}?reference={reference}",
        )

    def verify(self, reference: str) -> Verification:
        amount = ManualGateway.charges.get(reference)
        if amount is None:
            return Verification(ok=False, reference=reference, status="unknown", error="No charge.")
        return Verification(
            ok=True,
            reference=reference,
            gateway_reference=f"manual-{reference}",
            amount=amount,
            status="success",
        )

    def verify_signature(self, body: bytes, headers) -> bool:
        return True

    def parse_webhook(self, payload: dict) -> Verification | None:
        return self.verify(payload.get("reference", ""))
