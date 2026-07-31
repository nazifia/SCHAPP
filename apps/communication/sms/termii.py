"""Termii adapter.

stdlib `urllib` rather than `requests`: one JSON POST does not justify a
dependency, and the Celery task already owns retries.
"""

import json
import logging
import urllib.error
import urllib.request

from .base import SmsBackend, SmsResult

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.ng.termii.com/api/sms/send"
TIMEOUT = 15


class TermiiBackend(SmsBackend):
    name = "termii"

    def send(self, to: str, message: str, *, route: str = "transactional") -> SmsResult:
        if not self.credentials:
            return SmsResult(ok=False, provider=self.name, error="No Termii API key configured.")

        payload = {
            "to": to.lstrip("+"),
            "from": self.sender_id,
            "sms": message,
            "type": "plain",
            # "dnd" is Termii's transactional route: the only one that lands on
            # numbers enrolled in the NCC Do-Not-Disturb list.
            "channel": "dnd" if route == "transactional" else "generic",
            "api_key": self.credentials,
        }
        request = urllib.request.Request(
            ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = json.loads(response.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            return SmsResult(ok=False, provider=self.name, error=f"HTTP {exc.code}")
        except Exception as exc:
            return SmsResult(ok=False, provider=self.name, error=exc.__class__.__name__)

        message_id = body.get("message_id", "")
        ok = bool(message_id) or str(body.get("code", "")).lower() == "ok"
        return SmsResult(
            ok=ok,
            provider=self.name,
            message_id=str(message_id),
            error="" if ok else str(body.get("message", "unknown provider error"))[:200],
            raw=body,
        )
