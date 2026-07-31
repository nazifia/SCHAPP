import logging

from .base import PushBackend, PushResult

logger = logging.getLogger(__name__)


class ConsolePushBackend(PushBackend):
    """Dev backend: logs instead of pushing."""

    name = "console"

    def send(self, token: str, title: str, body: str, *, data: dict | None = None) -> PushResult:
        logger.info("push", extra={"title": title, "token_tail": token[-6:] if token else ""})
        return PushResult(ok=True, provider=self.name, message_id="console")


class LocMemPushBackend(PushBackend):
    """Test backend: keeps everything sent in `LocMemPushBackend.outbox`."""

    name = "locmem"
    outbox: list[dict] = []

    def send(self, token: str, title: str, body: str, *, data: dict | None = None) -> PushResult:
        LocMemPushBackend.outbox.append(
            {"token": token, "title": title, "body": body, "data": data or {}}
        )
        return PushResult(
            ok=True, provider=self.name, message_id=str(len(LocMemPushBackend.outbox))
        )
