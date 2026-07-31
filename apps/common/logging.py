"""Structured logging with NDPR-driven PII redaction.

Nigerian data-protection rules (NDPR / NDPA 2023) treat phone numbers and NINs
as personal data. Log files get shipped to third parties (Sentry, log hosts),
so redaction happens at the logging layer, not at every call site.
"""

import json
import logging
import re
from datetime import UTC, datetime

# +2348031234567 / 08031234567 / 8031234567
_MSISDN = re.compile(r"(?<!\d)(?:\+?234|0)?[789][01]\d{8}(?!\d)")
# 11-digit NIN, only when labelled — bare 11-digit runs are usually MSISDNs.
_NIN = re.compile(r"(?i)\b(nin\W{0,3})(\d{11})\b")
_OTP = re.compile(r"(?i)\b(otp|code|pin)(\W{0,3})(\d{4,8})\b")

_REDACTED = "[redacted]"


def redact(text: str) -> str:
    text = _NIN.sub(lambda m: m.group(1) + _REDACTED, text)
    text = _OTP.sub(lambda m: m.group(1) + m.group(2) + _REDACTED, text)
    return _MSISDN.sub(_REDACTED, text)


class RedactPIIFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(redact(str(a)) for a in record.args)
        except Exception:  # never let logging break the request
            pass
        return True


class JSONFormatter(logging.Formatter):
    """One JSON object per line, with the correlation id when present."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            # Third-party loggers (Celery's task tracer, for one) sometimes pass
            # args that do not match their own format string. A logging call is
            # never worth crashing the code that made it.
            message = record.msg
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for attr in ("request_id", "tenant", "user_id", "path", "status"):
            value = getattr(record, attr, None)
            if value is not None:
                payload[attr] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)
