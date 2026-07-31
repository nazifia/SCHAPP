"""Staff records and the logins that hang off them.

Over HTTP, because the two permissions involved — one for the personnel file,
one for who may sign in — are the feature.
"""

import pytest

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.people.models import Staff, StaffStatus
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

STAFF = "/api/v1/people/staff/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("queens-college")


def _headers(school, user):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def admin(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        yield user


@pytest.fixture
def bursar(school):
    """Holds `people.manage_staff` but not `admin.manage_users`."""
    with schema_context(school.schema_name):
        role = Role.objects.create(
            code="hr_clerk",
            name="HR Clerk",
            permissions=["people.view_staff", "people.manage_staff"],
        )
        user = User.objects.create_user("+2348032222222", first_name="Clerk")
        user.roles.add(role)
        yield user


@pytest.fixture
def teacher_record(school):
    with schema_context(school.schema_name):
        yield Staff.objects.create(
            staff_number="STF/001",
            first_name="Ngozi",
            last_name="Okeke",
            phone="+2348039999999",
        )


def test_onboarding_a_teacher_gives_them_a_login_and_a_role(client, school, admin, teacher_record):
    response = client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **_headers(school, admin),
    )

    assert response.status_code == 200, response.content
    assert response.json()["account_roles"] == ["teacher"]

    with schema_context(school.schema_name):
        staff = Staff.objects.get(pk=teacher_record.pk)
        # The join that makes `staff_for()` — my classes, my register, an
        # admissions decision — find anything at all.
        assert staff.user is not None
        assert staff.user.phone == "+2348039999999"
        assert staff.user.has_usable_password() is False


def test_an_existing_account_is_matched_not_duplicated(client, school, admin, teacher_record):
    """A teacher who is also a parent here already has an account."""
    with schema_context(school.schema_name):
        existing = User.objects.create_user("+2348039999999", first_name="Ngozi")

    client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **_headers(school, admin),
    )

    with schema_context(school.schema_name):
        assert User.objects.filter(phone="+2348039999999").count() == 1
        assert Staff.objects.get(pk=teacher_record.pk).user_id == existing.pk


def test_one_account_cannot_serve_two_staff_records(client, school, admin, teacher_record):
    headers = _headers(school, admin)
    client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **headers,
    )
    with schema_context(school.schema_name):
        twin = Staff.objects.create(
            staff_number="STF/002",
            first_name="Duplicate",
            last_name="Entry",
            phone="+2348039999999",
        )

    response = client.post(
        f"{STAFF}{twin.pk}/account/", {}, content_type="application/json", **headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ACCOUNT_IN_USE"


def test_a_staff_record_with_no_phone_says_so(client, school, admin):
    with schema_context(school.schema_name):
        caretaker = Staff.objects.create(
            staff_number="STF/003", first_name="Musa", last_name="Bello"
        )

    response = client.post(
        f"{STAFF}{caretaker.pk}/account/",
        {},
        content_type="application/json",
        **_headers(school, admin),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "STAFF_HAS_NO_PHONE"


def test_managing_personnel_files_does_not_mean_granting_logins(
    client, school, bursar, teacher_record
):
    """The privilege boundary: HR edits the record, not who may sign in."""
    headers = _headers(school, bursar)

    edit = client.patch(
        f"{STAFF}{teacher_record.pk}/",
        {"designation": "Head of Science"},
        content_type="application/json",
        **headers,
    )
    assert edit.status_code == 200, edit.content

    grant = client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["school_admin"]},
        content_type="application/json",
        **headers,
    )
    assert grant.status_code == 403
    assert grant.json()["error"]["code"] == "PERMISSION_DENIED"


def test_an_exit_closes_the_login_and_kills_live_sessions(client, school, admin, teacher_record):
    from apps.auth_phone import tokens

    headers = _headers(school, admin)
    client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **headers,
    )
    with schema_context(school.schema_name):
        account = Staff.objects.get(pk=teacher_record.pk).user
        pair = tokens.issue_for_user(account, tenant=school)

    response = client.patch(
        f"{STAFF}{teacher_record.pk}/",
        {"status": StaffStatus.EXITED},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content

    with schema_context(school.schema_name):
        staff = Staff.objects.get(pk=teacher_record.pk)
        staff.user.refresh_from_db()
        assert staff.user.is_active is False
        assert staff.date_exited is not None
        # The part that would otherwise survive: a refresh family left alive
        # hands the old phone its session back on reinstatement.
        assert staff.user.token_families.filter(revoked_at__isnull=True).count() == 0
        assert AuditLog.objects.filter(action="admin.role.assigned").exists()

    refreshed = client.post(
        "/api/v1/auth/token/refresh/",
        {"refresh": pair["refresh"]},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
    )
    assert refreshed.status_code in {400, 401}


def test_reinstatement_reopens_the_login_but_not_the_powers(client, school, admin, teacher_record):
    headers = _headers(school, admin)
    client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **headers,
    )
    for status in (StaffStatus.EXITED, StaffStatus.ACTIVE):
        client.patch(
            f"{STAFF}{teacher_record.pk}/",
            {"status": status},
            content_type="application/json",
            **headers,
        )

    with schema_context(school.schema_name):
        staff = Staff.objects.get(pk=teacher_record.pk)
        staff.user.refresh_from_db()
        assert staff.user.is_active is True
        assert staff.date_exited is None
        # Roles were never stripped, so nothing to restore — but coming back in
        # a different post is a deliberate grant, not a side effect.
        assert sorted(staff.user.roles.values_list("code", flat=True)) == ["teacher"]


def test_the_staff_list_says_who_can_sign_in(client, school, admin, teacher_record):
    headers = _headers(school, admin)

    before = client.get(STAFF, **headers).json()["results"]
    assert before[0]["has_account"] is False

    client.post(
        f"{STAFF}{teacher_record.pk}/account/",
        {"roles": ["teacher"]},
        content_type="application/json",
        **headers,
    )
    after = client.get(STAFF, **headers).json()["results"]
    assert after[0]["has_account"] is True
