"""User, role, settings and audit administration.

Over HTTP, because the point of this surface is that it exists outside the
Django admin: the permission checks and the tenant header are the feature.
"""

import pytest

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.tenants.db import schema_context
from apps.tenants.models import TenantConfiguration

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

USERS = "/api/v1/admin/users/"
ROLES = "/api/v1/admin/roles/"
AUDIT = "/api/v1/admin/audit/entries/"
SETTINGS = "/api/v1/settings/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


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
def teacher(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348032222222", first_name="Teacher")
        user.roles.add(Role.objects.get(code="teacher"))
        yield user


def test_an_administrator_creates_a_user_and_gives_them_a_role(client, school, admin):
    response = client.post(
        USERS,
        {"phone": "08033333333", "first_name": "Ngozi", "roles": ["teacher"]},
        content_type="application/json",
        **_headers(school, admin),
    )

    assert response.status_code == 201, response.content
    body = response.json()
    assert body["roles"] == ["teacher"]
    # The point of the whole exercise: the account can now do teacher things.
    assert "assessment.enter_score" in body["permissions"]


def test_creating_a_user_writes_an_audit_entry(client, school, admin):
    client.post(
        USERS,
        {"phone": "08033333333", "roles": ["teacher"]},
        content_type="application/json",
        **_headers(school, admin),
    )

    with schema_context(school.schema_name):
        entry = AuditLog.objects.filter(action="admin.role.assigned").first()

    assert entry is not None
    assert entry.after == {"roles": ["teacher"]}


def test_roles_are_replaced_as_a_whole_set(client, school, admin, teacher):
    """`PUT roles/` is the only way roles change, so it has to actually work.

    It did not: the viewset's `http_method_names` left PUT out, and the action
    that declares `methods=["put"]` answered 405 to every call.
    """
    headers = _headers(school, admin)

    response = client.put(
        f"{USERS}{teacher.pk}/roles/",
        {"roles": ["registrar"]},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    assert response.json()["roles"] == ["registrar"]

    with schema_context(school.schema_name):
        teacher.refresh_from_db()
        assert sorted(teacher.roles.values_list("code", flat=True)) == ["registrar"]

    # The record itself still refuses PUT: a full replace would blank the
    # phone number the account signs in with.
    assert (
        client.put(
            f"{USERS}{teacher.pk}/", {}, content_type="application/json", **headers
        ).status_code
        == 405
    )


def test_deactivating_a_user_kills_their_live_sessions(client, school, admin, teacher):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        tokens.issue_for_user(teacher, tenant=school)

    response = client.patch(
        f"{USERS}{teacher.pk}/",
        {"is_active": False},
        content_type="application/json",
        **_headers(school, admin),
    )
    assert response.status_code == 200, response.content

    with schema_context(school.schema_name):
        teacher.refresh_from_db()
        assert teacher.is_active is False
        assert teacher.token_families.filter(revoked_at__isnull=True).count() == 0


def test_users_can_be_searched_by_name_or_local_number(client, school, admin, teacher):
    headers = _headers(school, admin)

    by_name = client.get(f"{USERS}?search=Teacher", **headers).json()["results"]
    assert [row["full_name"] for row in by_name] == ["Teacher"]

    # The display column is grouped ("0803 222 2222"), the E.164 one is not,
    # so an unspaced run of digits has to match through `phone`.
    by_number = client.get(f"{USERS}?search=8032222222", **headers).json()["results"]
    assert len(by_number) == 1
    assert client.get(f"{USERS}?search=0803 222", **headers).json()["results"]


def test_a_teacher_cannot_administer_users(client, school, teacher):
    response = client.get(USERS, **_headers(school, teacher))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_an_unknown_role_is_refused_by_name(client, school, admin):
    response = client.post(
        USERS,
        {"phone": "08033333333", "roles": ["headmaster"]},
        content_type="application/json",
        **_headers(school, admin),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_ROLE"


def test_a_school_may_invent_a_role_but_not_a_permission(client, school, admin):
    headers = _headers(school, admin)

    created = client.post(
        ROLES,
        {
            "code": "head_of_exams",
            "name": "Head of Exams",
            "permissions": ["assessment.publish_results"],
        },
        content_type="application/json",
        **headers,
    )
    assert created.status_code == 201, created.content

    invented = client.post(
        ROLES,
        {"code": "wizard", "name": "Wizard", "permissions": ["assessment.do_magic"]},
        content_type="application/json",
        **headers,
    )
    # An invented code would look like a granted power and be checked by nothing.
    assert invented.status_code == 400
    assert invented.json()["error"]["code"] == "UNKNOWN_PERMISSION"


def test_a_system_role_cannot_be_deleted(client, school, admin):
    response = client.delete(f"{ROLES}teacher/", **_headers(school, admin))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "SYSTEM_ROLE"


def test_the_catalogue_lists_what_the_app_actually_checks(client, school, admin):
    body = client.get(f"{ROLES}catalogue/", **_headers(school, admin)).json()
    codes = [row["code"] for row in body["permissions"]]

    assert "assessment.enter_score" in codes
    assert body["wildcard"] == "*"


def test_settings_round_trip_and_are_audited(client, school, admin):
    headers = _headers(school, admin)

    response = client.patch(
        SETTINGS,
        {"motto": "Knowledge and Character", "label_overrides": {"TERM": "Quarter"}},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    assert response.json()["motto"] == "Knowledge and Character"

    configuration = TenantConfiguration.objects.get(tenant=school)
    assert configuration.label_overrides["TERM"] == "Quarter"

    with schema_context(school.schema_name):
        assert AuditLog.objects.filter(action="admin.settings.changed").exists()


def test_the_audit_trail_is_readable_and_still_append_only(client, school, admin):
    headers = _headers(school, admin)
    client.post(
        USERS,
        {"phone": "08033333333", "roles": ["teacher"]},
        content_type="application/json",
        **headers,
    )

    listing = client.get(f"{AUDIT}?action=admin.role", **headers)
    assert listing.status_code == 200
    rows = listing.json()["results"]
    assert rows and rows[0]["action_display"] == "Role assigned"

    # Read-only by construction: the model refuses to be written through.
    assert client.post(AUDIT, {}, content_type="application/json", **headers).status_code == 405
