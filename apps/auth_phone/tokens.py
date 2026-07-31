"""JWT issue, rotation and reuse detection."""

import logging
from contextlib import nullcontext

from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken, UntypedToken

from apps.accounts.models import TokenFamily
from apps.accounts.platform import PLATFORM_CLAIM, is_platform_user, platform_context
from apps.tenants.db import schema_context

logger = logging.getLogger(__name__)

FAMILY_CLAIM = "family"
TENANT_SLUG_CLAIM = "tenant_slug"
TENANT_SCHEMA_CLAIM = "tenant_schema"


class TokenReuseDetected(Exception):
    """A refresh token was presented after it had already been rotated."""


def _stamp(token, *, user, tenant, family, device) -> None:
    token[TENANT_SLUG_CLAIM] = getattr(tenant, "slug", "") or ""
    token[TENANT_SCHEMA_CLAIM] = getattr(tenant, "schema_name", "") or ""
    # Which database holds the user, not what they may do: a platform
    # superuser's row is in `default` however many schools they visit.
    token[PLATFORM_CLAIM] = is_platform_user(user)
    token[FAMILY_CLAIM] = str(family.pk)
    token["device_id"] = getattr(device, "device_id", "") or ""
    # Roles ride in the access token so the app can render its shell without a
    # second round-trip on a slow connection. Authorisation still re-checks
    # server-side; this is for the UI, not for the gate.
    token["roles"] = sorted(r.code for r in user.roles.all())


def issue_for_user(user, *, tenant, device=None) -> dict:
    # The family and simplejwt's own outstanding-token row both key on the
    # user, so they are written wherever that user's row lives — the school's
    # database for its own staff, the platform's for a superuser reaching in.
    with platform_context(user):
        family = TokenFamily.objects.create(user=user, device=device, last_used_at=timezone.now())
        refresh = RefreshToken.for_user(user)
        _stamp(refresh, user=user, tenant=tenant, family=family, device=device)
        access = refresh.access_token
        _stamp(access, user=user, tenant=tenant, family=family, device=device)
    return {
        "access": str(access),
        "refresh": str(refresh),
        "family": str(family.pk),
    }


def rotate(raw_refresh: str, *, tenant) -> dict:
    """Verify, rotate and re-issue. Raises on reuse or a dead family."""
    # A platform superuser's family and blacklist rows are in the platform
    # database, and the school selected by this request is not it.
    with _token_context(raw_refresh):
        return _rotate(raw_refresh, tenant=tenant)


def _token_context(raw_refresh: str):
    claims = verified_claims(raw_refresh)
    return schema_context(None) if claims.get("platform") else nullcontext()


def _rotate(raw_refresh: str, *, tenant) -> dict:
    try:
        refresh = RefreshToken(raw_refresh)
    except TokenError as exc:
        # Distinguish "already used" from "garbage": a signature-valid token
        # that only fails the blacklist check is a replay, and the whole
        # family has to die because we cannot tell attacker from victim.
        if _signature_is_valid(raw_refresh):
            _revoke_family_from(raw_refresh, reason="refresh token reuse detected")
            raise TokenReuseDetected() from exc
        raise

    family = _family_for(refresh)
    if family is None or not family.is_active:
        raise TokenError("Session has been revoked.")

    # The account itself, not just the session. Every code path that
    # deactivates a user through the API also revokes their families
    # (`UserViewSet.partial_update`, `set_staff_status`) — but the Django admin
    # writes `is_active` straight to the row, and so would a support script.
    # Checking here covers all of them at the one point a session is renewed,
    # which is what stops a disabled account quietly holding a live family for
    # thirty days and getting every old device back the day it is reinstated.
    if not family.user.is_active:
        raise TokenError("This account is no longer active.")

    claimed_slug = refresh.get(TENANT_SLUG_CLAIM)
    if claimed_slug and tenant is not None and claimed_slug != tenant.slug:
        raise TokenError("Session belongs to a different institution.")

    user = family.user
    refresh.blacklist()  # old token is spent from here on

    new_refresh = RefreshToken.for_user(user)
    _stamp(new_refresh, user=user, tenant=tenant, family=family, device=family.device)
    access = new_refresh.access_token
    _stamp(access, user=user, tenant=tenant, family=family, device=family.device)

    family.last_used_at = timezone.now()
    family.save(update_fields=["last_used_at", "updated_at"])

    return {"access": str(access), "refresh": str(new_refresh), "family": str(family.pk)}


def revoke_family(family_id: str, reason: str) -> None:
    family = TokenFamily.objects.filter(pk=family_id).first()
    if family:
        family.revoke(reason)


def revoke_all_for_user(user, reason: str, *, except_family: str = "") -> int:
    """Kill every live session for a user, and say how many died.

    `except_family` spares the caller's own, which is what makes this usable
    from a PIN change: re-securing an account must sign out the other devices
    without signing out the device doing the securing.
    """
    # Through the user, not the manager: related rows are read from the
    # database that holds the row they belong to, which for a platform
    # superuser is never the school they are currently inside.
    families = user.token_families.filter(revoked_at__isnull=True)
    if except_family:
        families = families.exclude(pk=except_family)
    return families.update(revoked_at=timezone.now(), revoked_reason=reason[:120])


# ---------------------------------------------------------------------------
def verified_claims(raw: str) -> dict:
    """The family and tenant a token *proves* it was minted for, or ``{}``.

    Only ever reads a token whose signature checks out. The body of a rejected
    token is attacker-controlled and this ends up in the audit trail, so the
    unverified payload is never touched.
    """
    try:
        claims = UntypedToken(raw)
    except TokenError:
        return {}
    return {
        "family": claims.get(FAMILY_CLAIM) or "",
        "tenant_slug": claims.get(TENANT_SLUG_CLAIM) or "",
        "platform": bool(claims.get(PLATFORM_CLAIM)),
    }


def _signature_is_valid(raw: str) -> bool:
    try:
        UntypedToken(raw)  # checks signature and expiry, not the blacklist
        return True
    except TokenError:
        return False


def _family_for(token) -> TokenFamily | None:
    family_id = token.get(FAMILY_CLAIM)
    if not family_id:
        return None
    return TokenFamily.objects.filter(pk=family_id).select_related("user", "device").first()


def _revoke_family_from(raw: str, *, reason: str) -> None:
    try:
        claims = UntypedToken(raw)
    except TokenError:
        return
    family_id = claims.get(FAMILY_CLAIM)
    if family_id:
        revoke_family(family_id, reason)
        logger.warning("refresh token reuse; family revoked", extra={"family": family_id})
