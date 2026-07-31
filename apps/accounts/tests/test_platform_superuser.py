"""A platform superuser signing in to a school they have no row in.

`accounts` is in both halves of the app split, so each school owns its own
`accounts_user` table and the platform's staff are in none of them. That made
the platform host the only address a superuser could ever sign in on: a
school's own `/admin/` and every API login carry a tenant, and the lookup
landed in a table where they did not exist.

These tests hold the bargain that makes the reach safe. The superuser is
resolved against the platform database on every request — so a demotion ends
the session immediately, and nothing is copied into a school to be cleaned up
later. Everything their session owns (challenge, device, token family, audit
row) is written on the platform side, because a school's foreign keys cannot
point out of its own database. And a school's own user always answers first for
their own school, even on the same number.

The last test here is not about superusers at all: user ids are per database,
so a token that names no tenant must not be spendable inside one.
"""

import re

import pytest
from django.core.cache import cache

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.auth_phone import tokens
from apps.auth_phone.models import OtpRequest
from apps.communication.sms.console import LocMemBackend
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

SUPER_PHONE = "+2348039000001"
STAFF_PHONE = "+2348039000002"
REQUEST_URL = "/api/v1/auth/otp/request/"
VERIFY_URL = "/api/v1/auth/otp/verify/"
ME_URL = "/api/v1/auth/me/"


@pytest.fixture(autouse=True)
def _clean_state():
    cache.clear()
    LocMemBackend.outbox.clear()
    yield
    cache.clear()
    LocMemBackend.outbox.clear()


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def superuser(db, ncc_table):
    return User.objects.create_superuser(
        SUPER_PHONE, password="platform-pass", first_name="Plat", last_name="Ops"
    )


@pytest.fixture
def platform_staff(db, ncc_table):
    """Staff on the platform, not a superuser — entitled to no school."""
    return User.objects.create_user(
        STAFF_PHONE, password="staff-pass", first_name="Desk", last_name="Agent", is_staff=True
    )


def _headers(school):
    return {"HTTP_X_TENANT_SLUG": school.slug}


def _last_code() -> str:
    match = re.search(r"\b(\d{6})\b", LocMemBackend.outbox[-1]["message"])
    assert match, "no code in the outbound SMS"
    return match.group(1)


def _otp_login(client, school, phone: str = SUPER_PHONE):
    response = client.post(
        REQUEST_URL, {"phone": phone}, content_type="application/json", **_headers(school)
    )
    assert response.status_code == 200
    return client.post(
        VERIFY_URL,
        {
            "request_id": response.json()["request_id"],
            "code": _last_code(),
            "device": {"device_id": "plat-1"},
        },
        content_type="application/json",
        **_headers(school),
    )


# ---------------------------------------------------------------------------
# The admin, on a school's own address
# ---------------------------------------------------------------------------
def test_a_superuser_signs_in_to_the_admin_on_a_school_host(client, superuser, school):
    host = school.domains.get().domain

    response = client.post(
        "/admin/login/",
        {"username": SUPER_PHONE, "password": "platform-pass", "next": "/admin/"},
        HTTP_HOST=host,
    )

    assert response.status_code == 302
    assert client.get("/admin/", HTTP_HOST=host).status_code == 200


def test_the_admin_session_on_a_school_host_reads_that_school(client, superuser, school):
    with schema_context(school.schema_name):
        Student.objects.create(admission_number="KC/001", first_name="Ada", last_name="Eze")
    host = school.domains.get().domain
    client.post(
        "/admin/login/",
        {"username": SUPER_PHONE, "password": "platform-pass", "next": "/admin/"},
        HTTP_HOST=host,
    )

    response = client.get("/admin/people/student/", HTTP_HOST=host)

    assert response.status_code == 200
    assert b"KC/001" in response.content


def test_platform_staff_are_not_let_in_on_a_school_host(client, platform_staff, school):
    """The backend answers for superusers only. Staff permissions are rows, and
    rows in the platform database mean nothing about a school's records.
    """
    host = school.domains.get().domain

    client.post(
        "/admin/login/",
        {"username": STAFF_PHONE, "password": "staff-pass", "next": "/admin/"},
        HTTP_HOST=host,
    )

    assert client.get("/admin/", HTTP_HOST=host).status_code in (302, 403)


# ---------------------------------------------------------------------------
# The API, against any school
# ---------------------------------------------------------------------------
def test_otp_login_into_a_school_returns_the_platform_account(client, superuser, school):
    response = _otp_login(client, school)

    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["full_name"] == "Plat Ops"


def test_the_login_is_written_on_the_platform_side(client, superuser, school):
    """A school's foreign keys cannot point at a row in another database."""
    assert _otp_login(client, school).status_code == 200

    assert OtpRequest.objects.filter(phone=SUPER_PHONE).exists()
    assert superuser.devices.filter(device_id="plat-1").exists()
    assert superuser.token_families.exists()

    with schema_context(school.schema_name):
        assert not OtpRequest.objects.filter(phone=SUPER_PHONE).exists()
        assert not User.objects.filter(phone=SUPER_PHONE).exists()


def test_the_session_reads_the_school_it_was_issued_for(client, superuser, school):
    with schema_context(school.schema_name):
        Student.objects.create(admission_number="KC/002", first_name="Bisi", last_name="Adeyemi")
    access = _otp_login(client, school).json()["access"]

    response = client.get(
        "/api/v1/people/students/", HTTP_AUTHORIZATION=f"Bearer {access}", **_headers(school)
    )

    assert response.status_code == 200
    assert [s["admission_number"] for s in response.json()["results"]] == ["KC/002"]


def test_the_entry_is_named_in_the_platform_trail(client, superuser, school):
    assert _otp_login(client, school).status_code == 200

    entry = AuditLog.objects.filter(action=AuditAction.LOGIN_OTP).latest("created_at")
    assert entry.actor_id == superuser.pk
    assert entry.tenant_slug == school.slug

    with schema_context(school.schema_name):
        assert not AuditLog.objects.filter(action=AuditAction.LOGIN_OTP).exists()


def test_a_demoted_superuser_loses_the_session_at_the_next_request(client, superuser, school):
    access = _otp_login(client, school).json()["access"]

    User.objects.filter(pk=superuser.pk).update(is_superuser=False)

    response = client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {access}", **_headers(school))
    assert response.status_code == 401


def test_the_schools_own_user_answers_first_on_the_same_number(client, superuser, school):
    """A superuser's number is not a claim on a number inside a school."""
    with schema_context(school.schema_name):
        User.objects.create_user(SUPER_PHONE, first_name="Amaka", last_name="Obi")

    response = _otp_login(client, school)

    assert response.json()["user"]["full_name"] == "Amaka Obi"
    with schema_context(school.schema_name):
        assert OtpRequest.objects.filter(phone=SUPER_PHONE).exists()


def test_a_tenantless_token_cannot_be_spent_inside_a_school(client, platform_staff, school):
    """Ids are per database: id 7 is a different person in every school, so a
    token that never named one must not be honoured inside one.
    """
    access = tokens.issue_for_user(platform_staff, tenant=None)["access"]

    response = client.get(ME_URL, HTTP_AUTHORIZATION=f"Bearer {access}", **_headers(school))

    assert response.status_code == 401
