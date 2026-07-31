import logging

from .base import SmsBackend, SmsResult

logger = logging.getLogger(__name__)


class ConsoleBackend(SmsBackend):
    """Dev backend. Prints instead of sending.

    The message body is printed raw on purpose — a developer needs to read the
    OTP. It goes to stdout, not through the logging pipeline, so it never
    reaches a log aggregator.
    """

    name = "console"

    def send(self, to: str, message: str, *, route: str = "transactional") -> SmsResult:
        print(f"\n[SMS:{route}] to {to} from {self.sender_id or 'SCHAPP'}\n  {message}\n")  # noqa: T201
        return SmsResult(ok=True, provider=self.name, message_id="console")


class LocMemBackend(SmsBackend):
    """Test backend: keeps everything sent in `LocMemBackend.outbox`."""

    name = "locmem"
    outbox: list[dict] = []

    def send(self, to: str, message: str, *, route: str = "transactional") -> SmsResult:
        LocMemBackend.outbox.append({"to": to, "message": message, "route": route})
        return SmsResult(ok=True, provider=self.name, message_id=str(len(LocMemBackend.outbox)))
