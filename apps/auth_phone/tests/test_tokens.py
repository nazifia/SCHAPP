"""Refresh rotation, reuse detection and cross-tenant token rejection."""

import pytest
from django.core.cache import cache

from apps.accounts.models import TokenFamily, User
from apps.auth_phone import tokens
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

REFRESH_URL = "/api/v1/auth/token/refresh/"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def user(school):
    with schema_context(school.schema_name):
        return User.objects.create_user("+2348031234567", first_name="Amaka")


def _issue(school, user):
    with schema_context(school.schema_name):
        return tokens.issue_for_user(user, tenant=school)


def test_tokens_carry_the_tenant_and_family_claims(school, user):
    from rest_framework_simplejwt.tokens import UntypedToken

    pair = _issue(school, user)
    claims = UntypedToken(pair["access"])

    assert claims["tenant_slug"] == school.slug
    assert claims["tenant_schema"] == school.schema_name
    assert claims["family"] == pair["family"]


def test_rotation_returns_a_new_pair_in_the_same_family(school, user):
    pair = _issue(school, user)
    with schema_context(school.schema_name):
        rotated = tokens.rotate(pair["refresh"], tenant=school)

    assert rotated["refresh"] != pair["refresh"]
    assert rotated["family"] == pair["family"]


def test_a_deactivated_account_cannot_refresh(school, user):
    """The API paths that deactivate a user revoke their families; the Django
    admin writes `is_active` straight to the row and does not.

    Without this check that account keeps a live family for thirty days, and
    reinstating it hands every old device its session back — the exact failure
    `UserViewSet.partial_update` guards against on its own path only."""
    from rest_framework_simplejwt.exceptions import TokenError

    pair = _issue(school, user)
    with schema_context(school.schema_name):
        User.objects.filter(pk=user.pk).update(is_active=False)  # as the admin does it
        assert TokenFamily.objects.filter(revoked_at__isnull=True).exists()

        with pytest.raises(TokenError):
            tokens.rotate(pair["refresh"], tenant=school)


def test_replaying_a_rotated_refresh_token_kills_the_whole_family(school, user):
    pair = _issue(school, user)
    with schema_context(school.schema_name):
        rotated = tokens.rotate(pair["refresh"], tenant=school)

        # The attacker replays the token the legitimate client already spent.
        with pytest.raises(tokens.TokenReuseDetected):
            tokens.rotate(pair["refresh"], tenant=school)

        family = TokenFamily.objects.get(pk=pair["family"])
        assert not family.is_active
        assert "reuse" in family.revoked_reason

        # And the legitimate client's newest token is dead too — we cannot
        # tell which side was the attacker.
        from rest_framework_simplejwt.exceptions import TokenError

        with pytest.raises(TokenError):
            tokens.rotate(rotated["refresh"], tenant=school)


def test_a_revoked_family_cannot_refresh(school, user):
    pair = _issue(school, user)
    with schema_context(school.schema_name):
        TokenFamily.objects.get(pk=pair["family"]).revoke("user logged out")
        from rest_framework_simplejwt.exceptions import TokenError

        with pytest.raises(TokenError):
            tokens.rotate(pair["refresh"], tenant=school)


def test_garbage_refresh_is_not_treated_as_reuse(school, user):
    with schema_context(school.schema_name):
        from rest_framework_simplejwt.exceptions import TokenError

        with pytest.raises(TokenError):
            tokens.rotate("not-a-token", tenant=school)


def test_a_token_from_another_school_is_refused(client, school, make_tenant, user):
    other = make_tenant("unity-poly", institution_type="TERTIARY")
    pair = _issue(school, user)

    response = client.get(
        "/api/v1/auth/me/",
        HTTP_X_TENANT_SLUG=other.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_MISMATCH"


def test_a_rejected_refresh_is_audited_with_its_reason(client, school, user):
    """The reuse branch was always audited; this one was silent, which made the
    common failure the one nobody could diagnose."""
    from apps.audit.models import AuditAction, AuditLog

    pair = _issue(school, user)
    with schema_context(school.schema_name):
        TokenFamily.objects.get(pk=pair["family"]).revoke("user logged out")

    response = client.post(
        REFRESH_URL,
        data={"refresh": pair["refresh"]},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"

    with schema_context(school.schema_name):
        entry = AuditLog.objects.filter(action=AuditAction.TOKEN_INVALID).get()
        assert not entry.succeeded
        assert "revoked" in entry.summary
        # The family is what makes the row actionable: it says *which* session.
        assert entry.object_id == pair["family"]


def test_a_cross_tenant_refresh_says_so_in_the_audit_trail(client, school, make_tenant, user):
    """The family lookup fails first, so the message alone reads "Session has
    been revoked." and hides that the token belongs to another school."""
    from apps.audit.models import AuditAction, AuditLog

    other = make_tenant("unity-poly", institution_type="TERTIARY")
    pair = _issue(school, user)

    response = client.post(
        REFRESH_URL,
        data={"refresh": pair["refresh"]},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=other.slug,
    )

    assert response.status_code == 401

    with schema_context(other.schema_name):
        entry = AuditLog.objects.filter(action=AuditAction.TOKEN_INVALID).get()
        assert school.slug in entry.summary
        assert other.slug in entry.summary


def test_an_unsigned_refresh_logs_nothing_it_cannot_verify(client, school, user):
    """A token whose signature does not check out has an attacker-controlled
    body, so no claim from it reaches the audit trail."""
    from apps.audit.models import AuditAction, AuditLog

    pair = _issue(school, user)
    head, payload, signature = pair["refresh"].split(".")
    forged = f"{head}.{payload}.{signature[:-4]}AAAA"

    response = client.post(
        REFRESH_URL,
        data={"refresh": forged},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
    )

    assert response.status_code == 401

    with schema_context(school.schema_name):
        entry = AuditLog.objects.filter(action=AuditAction.TOKEN_INVALID).get()
        assert entry.object_id == ""
        assert school.slug not in entry.summary


def test_a_stale_bearer_on_the_refresh_call_is_audited(client, school, user):
    """DRF authenticates before permissions, so an unusable bearer token on this
    AllowAny route 401s before the view runs. That is the failure the client hit
    in the field, and the view-level audit cannot see it."""
    from apps.audit.models import AuditAction, AuditLog

    pair = _issue(school, user)
    head, payload, signature = pair["access"].split(".")
    stale = f"{head}.{payload}.{signature[:-4]}AAAA"

    response = client.post(
        REFRESH_URL,
        data={"refresh": pair["refresh"]},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {stale}",
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_NOT_VALID"

    with schema_context(school.schema_name):
        entry = AuditLog.objects.filter(action=AuditAction.TOKEN_INVALID).get()
        assert not entry.succeeded
        assert "before the view ran" in entry.summary


def test_an_ordinary_expired_token_is_not_audited(client, school, user):
    """A 401 anywhere else is how a client learns to refresh. Auditing those
    would write a row per user per access-token lifetime."""
    from apps.audit.models import AuditAction, AuditLog

    pair = _issue(school, user)
    head, payload, signature = pair["access"].split(".")
    stale = f"{head}.{payload}.{signature[:-4]}AAAA"

    response = client.get(
        "/api/v1/auth/me/",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {stale}",
    )

    assert response.status_code == 401

    with schema_context(school.schema_name):
        assert not AuditLog.objects.filter(action=AuditAction.TOKEN_INVALID).exists()


def test_revoking_a_device_revokes_its_sessions(school, user):
    from apps.accounts.services import register_device

    with schema_context(school.schema_name):
        device, _ = register_device(user, device_id="dev-1", name="Tecno Spark")
        pair = tokens.issue_for_user(user, tenant=school, device=device)

        device.revoke("lost phone")

        from rest_framework_simplejwt.exceptions import TokenError

        with pytest.raises(TokenError):
            tokens.rotate(pair["refresh"], tenant=school)


def test_changing_the_pin_signs_out_the_other_devices_but_not_this_one(client, school, user):
    """Whoever knew the old PIN must not keep their session after it changes."""
    with schema_context(school.schema_name):
        user.set_pin("428193")
        user.save(update_fields=["pin_hash", "pin_set_at", "updated_at"])
        here = tokens.issue_for_user(user, tenant=school)
        elsewhere = tokens.issue_for_user(user, tenant=school)

    response = client.post(
        "/api/v1/auth/pin/set/",
        data={"pin": "739284", "confirm_pin": "739284"},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {here['access']}",
    )

    assert response.status_code == 200
    assert response.json()["sessions_ended"] == 1

    from rest_framework_simplejwt.exceptions import TokenError

    with schema_context(school.schema_name):
        # The other device is out...
        with pytest.raises(TokenError):
            tokens.rotate(elsewhere["refresh"], tenant=school)
        # ...and the one that made the change is still in.
        assert tokens.rotate(here["refresh"], tenant=school)["family"] == here["family"]


def test_deactivating_a_user_revokes_their_sessions(school, user):
    from apps.accounts.services import assign_role, seed_roles

    with schema_context(school.schema_name):
        seed_roles()
        admin = User.objects.create_user("+2348039999999", first_name="Bola")
        assign_role(admin, "school_admin")
        admin_pair = tokens.issue_for_user(admin, tenant=school)
        victim = tokens.issue_for_user(user, tenant=school)

    from django.test import Client

    response = Client().patch(
        f"/api/v1/admin/users/{user.pk}/",
        data={"is_active": False},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {admin_pair['access']}",
    )

    assert response.status_code == 200

    from rest_framework_simplejwt.exceptions import TokenError

    with schema_context(school.schema_name), pytest.raises(TokenError):
        tokens.rotate(victim["refresh"], tenant=school)
