"""Rate limiting is pure cache arithmetic — no database, no tenant."""

import pytest
from django.core.cache import cache

from apps.auth_phone import ratelimit


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_first_hit_is_allowed_and_reports_remaining():
    result = ratelimit.hit("otp_phone_hour", "+2348031234567")
    assert result.allowed
    assert result.remaining == 4  # 5 per hour


def test_limit_blocks_once_exhausted_and_gives_a_retry_after():
    phone = "+2348031234567"
    for _ in range(5):
        assert ratelimit.hit("otp_phone_hour", phone)

    blocked = ratelimit.hit("otp_phone_hour", phone)
    assert not blocked
    assert 0 < blocked.retry_after <= 3600


def test_the_60_second_burst_limit_allows_exactly_one():
    phone = "+2348031234567"
    assert ratelimit.hit("otp_phone_burst", phone)
    assert not ratelimit.hit("otp_phone_burst", phone)


def test_limits_are_per_identifier():
    assert ratelimit.hit("otp_phone_burst", "+2348031234567")
    assert ratelimit.hit("otp_phone_burst", "+2348039999999")


def test_settings_override_the_defaults(settings):
    settings.OTP_RATE_LIMITS = {"otp_phone_hour": (2, 3600)}
    phone = "+2348031234567"
    assert ratelimit.hit("otp_phone_hour", phone)
    assert ratelimit.hit("otp_phone_hour", phone)
    assert not ratelimit.hit("otp_phone_hour", phone)


def test_resend_delay_backs_off_exponentially():
    phone = "+2348031234567"
    delays = []
    for _ in range(4):
        ratelimit.hit("otp_phone_hour", phone)
        delays.append(ratelimit.resend_delay(phone))
    assert delays == [60, 120, 240, 480]


def test_resend_delay_is_capped():
    phone = "+2348031234567"
    for _ in range(20):
        ratelimit.hit("otp_phone_hour", phone)
    assert ratelimit.resend_delay(phone) == 900


def test_lockout_reports_remaining_time_and_clears():
    phone = "+2348031234567"
    assert ratelimit.is_locked_out(phone) == 0

    ratelimit.lock_out(phone, 1800)
    assert 0 < ratelimit.is_locked_out(phone) <= 1800

    ratelimit.clear_lockout(phone)
    assert ratelimit.is_locked_out(phone) == 0
