"""Pluggable SMS delivery.

Two things matter more than the choice of provider in Nigeria:

* **DND.** Most subscribers are enrolled on the Do-Not-Disturb list, which
  blocks promotional routes outright. OTP traffic must go over the
  transactional/corporate route or a large share of users simply never
  receive their code and blame the app.
* **Sender ID.** Alphanumeric sender IDs must be pre-registered with the
  operators. An unregistered ID is silently dropped.

Backends therefore always declare a route, and `send()` returns a result
rather than raising, so the caller can fall back to another channel.
"""

from dataclasses import dataclass, field
from importlib import import_module

from django.conf import settings


@dataclass
class SmsResult:
    ok: bool
    provider: str = ""
    message_id: str = ""
    error: str = ""
    #: Whatever the provider echoed back, for the delivery-report webhook.
    raw: dict = field(default_factory=dict)


class SmsBackend:
    """Interface. `route="transactional"` is what reaches DND numbers."""

    name = "base"

    def __init__(self, *, sender_id: str = "", credentials: str = "", **options):
        self.sender_id = sender_id or getattr(settings, "SMS_DEFAULT_SENDER_ID", "")
        self.credentials = credentials
        self.options = options

    def send(self, to: str, message: str, *, route: str = "transactional") -> SmsResult:
        raise NotImplementedError


def get_backend(path: str | None = None, **kwargs) -> SmsBackend:
    """Resolve `"apps.communication.sms.console.ConsoleBackend"` to an instance."""
    dotted = path or getattr(
        settings, "SMS_BACKEND", "apps.communication.sms.console.ConsoleBackend"
    )
    module_path, _, class_name = dotted.rpartition(".")
    backend_class = getattr(import_module(module_path), class_name)
    return backend_class(**kwargs)
