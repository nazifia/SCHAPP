"""OTP issue and verification.

Two rules shape everything here:

* **No user enumeration.** Requesting a code for a number that has no account
  returns the same body, the same status and a real `request_id`. The only
  difference is that no SMS leaves the building.
* **The plaintext code is never persisted or logged.** It exists in memory,
  in the SMS, and nowhere else.
"""

import hmac
import logging
import secrets
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

from django.conf import settings
from django.utils import timezone

from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.numbering.msisdn import InvalidMsisdn, mask, normalize
from apps.tenants.db import current_schema, is_public, on_commit, schema_context, tenant_atomic

from . import ratelimit
from .models import OtpPurpose, OtpRequest

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
OTP_TTL_SECONDS = 300
MAX_VERIFY_ATTEMPTS = 5


class OtpError(Exception):
    def __init__(self, code: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.code, self.message, self.retry_after = code, message, retry_after


@dataclass
class OtpIssued:
    request_id: str
    expires_in: int
    resend_after: int
    masked_phone: str


def generate_code() -> str:
    """Cryptographically random, zero-padded, uniform across 000000-999999."""
    return f"{secrets.randbelow(10**CODE_LENGTH):0{CODE_LENGTH}d}"


def hash_code(request_id: str, code: str) -> str:
    """HMAC-SHA256 keyed by SECRET_KEY.

    Argon2 would also work, but the value being protected is a 6-digit number
    with a 5-minute life: a keyed MAC makes an offline database-only attack
    impossible, which is the actual threat, and costs microseconds on a path
    that must stay fast under a results-day stampede.
    """
    return hmac.new(
        settings.SECRET_KEY.encode(), f"{request_id}:{code}".encode(), sha256
    ).hexdigest()


def _enforce_request_limits(phone: str, ip: str | None, tenant_slug: str) -> None:
    checks = [
        ("otp_phone_burst", phone),
        ("otp_phone_hour", phone),
        ("otp_phone_day", phone),
    ]
    if ip:
        checks.append(("otp_ip_hour", ip))
    if tenant_slug:
        checks.append(("otp_tenant_hour", tenant_slug))

    for name, identifier in checks:
        result = ratelimit.hit(name, identifier)
        if not result:
            raise OtpError(
                "OTP_RATE_LIMITED",
                "Too many code requests. Please wait before trying again.",
                result.retry_after,
            )


def request_otp(
    raw_phone: str,
    *,
    purpose: str = OtpPurpose.LOGIN,
    ip: str | None = None,
    user_agent: str = "",
    tenant=None,
    request=None,
) -> OtpIssued:
    with _login_context(raw_phone):
        return _request_otp(
            raw_phone, purpose=purpose, ip=ip, user_agent=user_agent, tenant=tenant, request=request
        )


def _login_context(raw_phone: str):
    """Which database this login is written to: the school's, or the platform's.

    A platform superuser has no row in any school, so their challenge cannot be
    stored in one either — `OtpRequest.user` is a foreign key and it would have
    to point across databases. Their whole login happens in the platform
    database and the school only names the token that comes out of it. The
    school's own users are unaffected, and they are checked first, so a school
    can have a member of staff on the same number as the platform's.
    """
    from apps.accounts.models import User
    from apps.accounts.platform import platform_superuser

    if is_public(current_schema()):
        return nullcontext()
    try:
        phone = normalize(raw_phone)
    except InvalidMsisdn:
        return nullcontext()  # let the real call produce the error
    if User.objects.filter(phone=phone, is_active=True).exists():
        return nullcontext()
    return schema_context(None) if platform_superuser(phone=phone) else nullcontext()


@tenant_atomic()
def _request_otp(
    raw_phone: str,
    *,
    purpose: str = OtpPurpose.LOGIN,
    ip: str | None = None,
    user_agent: str = "",
    tenant=None,
    request=None,
) -> OtpIssued:
    try:
        phone = normalize(raw_phone)
    except InvalidMsisdn as exc:
        raise OtpError(exc.code, exc.message) from exc

    locked = ratelimit.is_locked_out(phone)
    if locked:
        raise OtpError(
            "OTP_LOCKED_OUT",
            "Too many incorrect codes. Try again later.",
            locked,
        )

    tenant_slug = getattr(tenant, "slug", "") or ""
    _enforce_request_limits(phone, ip, tenant_slug)

    # One live code per phone+purpose: issuing a new one kills the old.
    OtpRequest.objects.filter(
        phone=phone, purpose=purpose, consumed_at__isnull=True, invalidated_at__isnull=True
    ).update(invalidated_at=timezone.now())

    from apps.accounts.models import User

    user = User.objects.filter(phone=phone, is_active=True).first()

    # code_hash is filled in by the worker, which is also where the plaintext
    # is generated: that way the code never travels through the broker and
    # never exists in the web process at all.
    otp = OtpRequest.objects.create(
        phone=phone,
        purpose=purpose,
        user=user,
        code_hash="",
        expires_at=timezone.now() + timedelta(seconds=OTP_TTL_SECONDS),
        ip=ip,
        user_agent=user_agent[:255],
    )

    if user is not None:
        # The worker selects a database from this slug, so it has to name the
        # one the challenge was just written to. A platform superuser's login
        # is in the platform database and gets the platform's SMS sender; the
        # school is still what the audit row below is named after.
        delivery_slug = "" if is_public(current_schema()) else tenant_slug
        on_commit(lambda: _dispatch(str(otp.pk), delivery_slug))
    else:
        # Unknown number: same response, no SMS, no hint to the caller.
        logger.info("otp requested for unknown number", extra={"tenant": tenant_slug})

    record(
        AuditAction.OTP_REQUESTED,
        request=request,
        actor=user,
        object_type="OtpRequest",
        object_id=str(otp.pk),
        summary=f"OTP requested for {mask(phone)} ({purpose})",
        tenant_slug=tenant_slug,
    )

    return OtpIssued(
        request_id=str(otp.pk),
        expires_in=OTP_TTL_SECONDS,
        resend_after=ratelimit.resend_delay(phone),
        masked_phone=mask(phone),
    )


def _dispatch(otp_id: str, tenant_slug: str) -> None:
    from .tasks import send_otp_sms

    send_otp_sms.delay(otp_id, tenant_slug)


@dataclass
class OtpVerified:
    user: object
    otp: OtpRequest


def verify_otp(request_id: str, code: str, *, request=None, tenant=None) -> OtpVerified:
    with _challenge_context(request_id):
        return _verify_otp(request_id, code, request=request, tenant=tenant)


def _challenge_context(request_id: str):
    """Find the database holding this challenge before opening a transaction.

    Verification arrives with nothing but an id, and the id alone cannot say
    which database it was written to. The school's own is checked first and
    answers for every ordinary login; only a challenge that is absent there and
    belongs to a superuser on the platform side sends verification elsewhere.
    """
    if not _looks_like_uuid(request_id) or is_public(current_schema()):
        return nullcontext()
    if OtpRequest.objects.filter(pk=request_id).exists():
        return nullcontext()
    with schema_context(None):
        reachable = OtpRequest.objects.filter(
            pk=request_id, user__is_superuser=True, user__is_active=True
        ).exists()
    return schema_context(None) if reachable else nullcontext()


@tenant_atomic()
def _verify_otp(request_id: str, code: str, *, request=None, tenant=None) -> OtpVerified:
    otp = (
        OtpRequest.objects.select_for_update().filter(pk=request_id).select_related("user").first()
        if _looks_like_uuid(request_id)
        else None
    )
    if otp is None:
        raise OtpError("OTP_INVALID", "That code is not valid. Request a new one.")

    locked = ratelimit.is_locked_out(otp.phone)
    if locked:
        raise OtpError("OTP_LOCKED_OUT", "Too many incorrect codes. Try again later.", locked)

    if otp.consumed_at is not None:
        raise OtpError("OTP_ALREADY_USED", "That code has already been used.")
    if otp.invalidated_at is not None:
        raise OtpError("OTP_INVALID", "That code is no longer valid. Request a new one.")
    if otp.expires_at <= timezone.now():
        raise OtpError("OTP_EXPIRED", "That code has expired. Request a new one.")

    submitted = (code or "").strip()

    # Dev-only master code, so a local client can log in without reading the
    # server console. Double-gated: DEBUG *and* a non-empty setting, so a
    # production settings module can never turn it on by accident.
    dev_code = settings.DEBUG and getattr(settings, "OTP_DEV_CODE", "")
    if dev_code and hmac.compare_digest(submitted, dev_code):
        return _consume(otp)

    attempt = ratelimit.hit("otp_verify_attempts", otp.phone)
    expected = hash_code(str(otp.pk), submitted)
    if not otp.code_hash or not hmac.compare_digest(expected, otp.code_hash):
        otp.attempts += 1
        otp.save(update_fields=["attempts", "updated_at"])
        # The counter is the rate limiter's, not the row's: this function is
        # transactional and every wrong code leaves it by raising, so the
        # `attempts` write above is rolled back and can never reach the
        # threshold. The limiter lives in the cache and survives the rollback.
        # `remaining == 0` means this attempt used the last of the allowance,
        # so the fifth wrong code locks — not the sixth.
        used = MAX_VERIFY_ATTEMPTS - attempt.remaining
        if not attempt or attempt.remaining == 0:
            seconds = ratelimit.lock_out(otp.phone)
            record(
                AuditAction.OTP_LOCKED,
                request=request,
                object_type="OtpRequest",
                object_id=str(otp.pk),
                summary=f"Locked out after {used} wrong codes",
                succeeded=False,
            )
            raise OtpError("OTP_LOCKED_OUT", "Too many incorrect codes. Try again later.", seconds)
        raise OtpError("OTP_INVALID", "That code is not correct.")

    return _consume(otp)


def _consume(otp: OtpRequest) -> OtpVerified:
    # A correct code for a number with no account still must not confirm the
    # number exists, so the failure looks identical to a wrong code.
    if otp.user is None:
        raise OtpError("OTP_INVALID", "That code is not correct.")

    otp.consumed_at = timezone.now()
    otp.save(update_fields=["consumed_at", "updated_at"])
    ratelimit.clear_lockout(otp.phone)
    ratelimit.reset("otp_phone_burst", otp.phone)

    user = otp.user
    if user.phone_verified_at is None:
        user.phone_verified_at = timezone.now()
        user.save(update_fields=["phone_verified_at", "updated_at"])

    return OtpVerified(user=user, otp=otp)


def _looks_like_uuid(value: str) -> bool:
    from uuid import UUID

    try:
        UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
