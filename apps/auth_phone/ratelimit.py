"""Redis-backed fixed-window rate limiting.

Fixed windows, not a sliding log: a school with 2,000 parents hammering
"resend" during results week needs this to cost one INCR, and the burst a
window boundary allows is harmless for OTP.

Every limit is a setting, because the right numbers differ between a 200-pupil
primary school and a 12,000-student polytechnic.
"""

import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

PREFIX = "rl"

DEFAULT_LIMITS = {
    # name: (max hits, window seconds)
    "otp_phone_burst": (1, 60),
    "otp_phone_hour": (5, 3600),
    "otp_phone_day": (10, 86400),
    "otp_ip_hour": (20, 3600),
    "otp_tenant_hour": (50, 3600),
    "otp_verify_attempts": (5, 1800),
    "pin_attempts": (5, 900),
}

#: Wrong-code lockout, applied once `otp_verify_attempts` is exhausted.
LOCKOUT_SECONDS = 1800


@dataclass
class LimitResult:
    allowed: bool
    remaining: int
    retry_after: int

    def __bool__(self) -> bool:
        return self.allowed


def limits() -> dict[str, tuple[int, int]]:
    return {**DEFAULT_LIMITS, **getattr(settings, "OTP_RATE_LIMITS", {})}


def _key(name: str, identifier: str) -> str:
    return f"{PREFIX}:{name}:{identifier}"


def hit(name: str, identifier: str, *, cost: int = 1) -> LimitResult:
    """Count one use and say whether it was allowed."""
    max_hits, window = limits()[name]
    key = _key(name, identifier)
    reset_key = key + ":reset"

    if cache.add(key, cost, window):
        cache.set(reset_key, int(time.time()) + window, window)
        count = cost
    else:
        try:
            count = cache.incr(key, cost)
        except ValueError:
            # Expired between the add and the incr; start a fresh window.
            cache.set(key, cost, window)
            cache.set(reset_key, int(time.time()) + window, window)
            count = cost

    if count <= max_hits:
        return LimitResult(True, max_hits - count, 0)
    return LimitResult(False, 0, retry_after(name, identifier))


def peek(name: str, identifier: str) -> int:
    return cache.get(_key(name, identifier), 0)


def retry_after(name: str, identifier: str) -> int:
    reset_at = cache.get(_key(name, identifier) + ":reset")
    if not reset_at:
        return limits()[name][1]
    return max(int(reset_at - time.time()), 1)


def reset(name: str, identifier: str) -> None:
    cache.delete(_key(name, identifier))
    cache.delete(_key(name, identifier) + ":reset")


def resend_delay(phone: str) -> int:
    """Exponential backoff on resends: 60s, 120s, 240s … capped at 15 minutes.

    Stops an impatient user (or a script) from burning a school's SMS credit
    while still letting a genuine "I didn't get it" retry through quickly.
    """
    used = peek("otp_phone_hour", phone)
    return min(60 * (2 ** max(used - 1, 0)), 900)


def is_locked_out(phone: str) -> int:
    """Seconds remaining on a wrong-code lockout, 0 if not locked."""
    key = _key("lockout", phone)
    until = cache.get(key)
    if not until:
        return 0
    remaining = int(until - time.time())
    return max(remaining, 0)


def lock_out(phone: str, seconds: int = LOCKOUT_SECONDS) -> int:
    cache.set(_key("lockout", phone), int(time.time()) + seconds, seconds)
    return seconds


def clear_lockout(phone: str) -> None:
    cache.delete(_key("lockout", phone))
    reset("otp_verify_attempts", phone)
