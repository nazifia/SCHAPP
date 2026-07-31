"""Pluggable payment gateways.

Same shape as `apps.communication.sms`: an interface, adapters that never
raise, and a resolver. What is specific to collecting school fees in Nigeria:

* **the reference is ours.** We generate it, hand it to the gateway, and read
  it back on both the verify call and the webhook. A gateway-generated id we
  then have to map back is one more table and one more way to lose a payment;
* **the webhook is not trusted.** Paystack signs the raw body; Flutterwave
  sends a shared hash. Either way the amount is re-read from the gateway's own
  payload, never from the browser that came back from checkout;
* **verify is always available.** Webhooks get lost. A bursar looking at a
  parent who says "I have paid" can force a verify on the reference, and it
  lands on exactly the same code path the webhook uses.

Keys live on `TenantConfiguration` (encrypted), so two schools on the same
deployment collect into their own accounts.
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from importlib import import_module

logger = logging.getLogger(__name__)

TIMEOUT = 20

#: dotted path per gateway name. A school picks one; both may be configured.
BACKENDS = {
    "paystack": "apps.finance.gateways.paystack.PaystackGateway",
    "flutterwave": "apps.finance.gateways.flutterwave.FlutterwaveGateway",
    "manual": "apps.finance.gateways.manual.ManualGateway",
}


@dataclass
class Charge:
    """What the client needs to send a parent to checkout."""

    ok: bool
    reference: str = ""
    checkout_url: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class Verification:
    """The gateway's verdict on one reference.

    `ok` means "this charge succeeded and the money is ours"; anything else,
    including a call that never reached the gateway, is not a payment.
    """

    ok: bool
    reference: str = ""
    gateway_reference: str = ""
    amount: Decimal = Decimal("0.00")
    status: str = ""
    error: str = ""
    raw: dict = field(default_factory=dict)


class GatewayNotConfigured(RuntimeError):
    pass


class PaymentGateway:
    """Interface. Adapters return results, they do not raise."""

    name = "base"

    def __init__(self, *, secret_key: str = "", public_key: str = "", **options):
        self.secret_key = secret_key
        self.public_key = public_key
        self.options = options

    # --- outbound ----------------------------------------------------------
    def initialize(
        self, *, reference: str, amount: Decimal, email: str, callback_url: str = "", **extra
    ) -> Charge:
        raise NotImplementedError

    def verify(self, reference: str) -> Verification:
        raise NotImplementedError

    # --- inbound -----------------------------------------------------------
    def verify_signature(self, body: bytes, headers) -> bool:
        """Is this webhook really from the gateway?"""
        raise NotImplementedError

    def parse_webhook(self, payload: dict) -> Verification | None:
        """Read a webhook body. None means "an event we do not act on"."""
        raise NotImplementedError

    # --- transport ---------------------------------------------------------
    def _request(self, url: str, *, method: str = "GET", payload: dict | None = None) -> dict:
        """One JSON call. Returns `{}` on any transport or decode failure.

        stdlib `urllib` rather than `requests`, matching the SMS adapters: two
        endpoints per gateway do not justify a dependency, and the caller
        already treats an empty body as "no verdict".
        """
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode() or "{}")
            except Exception:
                logger.warning("gateway http error", extra={"gateway": self.name, "code": exc.code})
                return {}
        except Exception as exc:
            logger.warning(
                "gateway unreachable", extra={"gateway": self.name, "error": exc.__class__.__name__}
            )
            return {}


def get_gateway(name: str, tenant=None) -> PaymentGateway:
    """Resolve a gateway by name, with this tenant's keys.

    `tenant` is the platform-schema `Tenant`; its `configuration` holds the
    encrypted keys. Passing none gives an unconfigured adapter, which is what
    the manual gateway and the tests want.
    """
    name = (name or "").strip().lower()
    dotted = BACKENDS.get(name)
    if dotted is None:
        raise GatewayNotConfigured(f"Unknown payment gateway: {name!r}")

    module_path, _, class_name = dotted.rpartition(".")
    gateway_class = getattr(import_module(module_path), class_name)

    configuration = getattr(tenant, "configuration", None)
    secret = getattr(configuration, f"{name}_secret_key", "") or ""
    public = getattr(configuration, f"{name}_public_key", "") or ""
    if name != "manual" and not secret:
        raise GatewayNotConfigured(f"No {name} keys are configured for this institution.")
    return gateway_class(secret_key=secret, public_key=public)


def configured_gateways(tenant=None) -> list[str]:
    """Which gateways this school could actually charge through."""
    configuration = getattr(tenant, "configuration", None)
    return [
        name
        for name in BACKENDS
        if name != "manual" and getattr(configuration, f"{name}_secret_key", "")
    ]
