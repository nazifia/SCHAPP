"""A platform superuser can reach into one school from the platform admin.

Before this, the admin on the platform host could only ever show the platform's
own tables — every tenant-only page was hidden by `TenantOnlyAdminMixin`,
because with no tenant selected there is genuinely no database behind it. That
made the admin safe and useless for the one job it is good at: looking at a
single school's records when someone asks for help.

These tests hold the three things that make handing a superuser that reach
defensible: only a superuser gets it, the session survives the switch it
causes, and every entry and exit is written to the platform trail.
"""

import pytest

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.people.models import Student
from apps.tenants.admin_switch import ADMIN_TENANT_SESSION_KEY
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

PLATFORM_HOST = "testserver"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def superuser(db, ncc_table):
    return User.objects.create_superuser(
        "+2348039000001", password="platform-pass", first_name="Plat", last_name="Ops"
    )


@pytest.fixture
def platform_staff(db, ncc_table):
    """Staff, admin-capable, *not* a superuser — the case the gate is for."""
    return User.objects.create_user(
        "+2348039000002",
        password="staff-pass",
        first_name="Desk",
        last_name="Agent",
        is_staff=True,
    )


def _login(client, user, password):
    assert client.login(phone=user.phone, password=password)


# ---------------------------------------------------------------------------
# Entering and leaving
# ---------------------------------------------------------------------------
def test_superuser_enters_a_tenant_and_the_selection_sticks(client, superuser, school):
    _login(client, superuser, "platform-pass")

    response = client.post(
        "/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST
    )

    assert response.status_code == 302
    assert client.session[ADMIN_TENANT_SESSION_KEY] == school.slug


def test_leaving_clears_the_selection(client, superuser, school):
    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    client.post("/admin/switch-tenant/", {"tenant": ""}, HTTP_HOST=PLATFORM_HOST)

    assert ADMIN_TENANT_SESSION_KEY not in client.session


def test_the_superuser_stays_logged_in_inside_a_tenant(client, superuser, school):
    """The trap this whole feature is built around.

    `apps.accounts` is listed in both halves of the app split, so the user
    table exists in the platform database *and* in every school's. Select the
    tenant before the session's user id is resolved and it resolves against a
    table the platform superuser has no row in — they are silently logged out
    on the page they just asked for.
    """
    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    response = client.get("/admin/", HTTP_HOST=PLATFORM_HOST)

    assert response.status_code == 200
    assert response.context["user"].is_authenticated


def test_an_unprovisioned_tenant_cannot_be_entered(client, superuser, school):
    """A tenant row can outlive its database. That must be a message, not an
    OperationalError on every admin page from then on."""
    from apps.tenants.db import drop_database

    drop_database(school.schema_name)
    _login(client, superuser, "platform-pass")

    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    assert ADMIN_TENANT_SESSION_KEY not in client.session


def test_an_offsite_next_is_not_followed(client, superuser, school):
    """`next` is a form field, so it is caller-controlled even though our own
    form is what normally sends it. Reflected unchecked it is an open redirect
    wearing an admin session."""
    _login(client, superuser, "platform-pass")

    response = client.post(
        "/admin/switch-tenant/",
        {"tenant": school.slug, "next": "https://evil.example.com/"},
        HTTP_HOST=PLATFORM_HOST,
    )

    assert response.status_code == 302
    assert response["Location"] == "/admin/"


def test_switching_is_post_only(client, superuser, school):
    _login(client, superuser, "platform-pass")
    response = client.get("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)
    assert response.status_code == 405
    assert ADMIN_TENANT_SESSION_KEY not in client.session


# ---------------------------------------------------------------------------
# Who is allowed
# ---------------------------------------------------------------------------
def test_staff_who_are_not_superusers_are_refused(client, platform_staff, school):
    """Permissions are rows, and rows live in whichever database is selected.

    A non-superuser's permissions would change meaning at the instant of the
    switch — the same account, judged against another school's grants. A
    superuser has none to change: `has_perm` short-circuits on `is_superuser`
    before it ever reaches a database.
    """
    _login(client, platform_staff, "staff-pass")

    response = client.post(
        "/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST
    )

    assert response.status_code == 403
    assert ADMIN_TENANT_SESSION_KEY not in client.session


def test_a_selection_is_dropped_when_the_superuser_is_demoted(client, superuser, school):
    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    User.objects.filter(pk=superuser.pk).update(is_superuser=False)
    client.get("/admin/", HTTP_HOST=PLATFORM_HOST)

    assert ADMIN_TENANT_SESSION_KEY not in client.session


# ---------------------------------------------------------------------------
# What it unlocks
# ---------------------------------------------------------------------------
def test_tenant_only_pages_are_hidden_until_a_tenant_is_selected(client, superuser, school):
    """Unreachable without a selection — but as the selector, not as a 403.

    The page really is forbidden with no tenant selected: there is no database
    behind it. Saying so with "403 Forbidden" tells a superuser they lack a
    permission they in fact hold, when all they are missing is the name of a
    school. They get sent to the one control that fixes it.
    """
    _login(client, superuser, "platform-pass")

    hidden = client.get("/admin/people/student/", HTTP_HOST=PLATFORM_HOST)
    assert hidden.status_code == 302
    assert hidden["Location"] == "/admin/"

    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    shown = client.get("/admin/people/student/", HTTP_HOST=PLATFORM_HOST)
    assert shown.status_code == 200


def test_a_non_superuser_still_gets_a_flat_403(client, platform_staff, school):
    """The redirect is a courtesy for the one account entitled to switch.
    Anyone else is refused, and refused the same way with or without a
    selection — no hint that the page exists behind a school."""
    _login(client, platform_staff, "staff-pass")

    response = client.get("/admin/people/student/", HTTP_HOST=PLATFORM_HOST)

    assert response.status_code == 403


def test_the_admin_reads_the_selected_school(client, superuser, school):
    with schema_context(school.schema_name):
        Student.objects.create(admission_number="KC/001", first_name="Ada", last_name="Okonkwo")

    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    response = client.get("/admin/people/student/", HTTP_HOST=PLATFORM_HOST)

    assert b"KC/001" in response.content


def test_writing_from_inside_a_tenant_lands_in_that_tenant(client, superuser, school):
    """Also the regression test for admin history content types.

    `LogEntry` is written to the platform database, but its `content_type` was
    resolved through the router — so from inside a tenant it carried an id from
    that school's `django_content_type` table, which lists different apps. The
    id either named the wrong model or did not exist in `default` at all, and
    the foreign key failed: saving *anything* from inside a tenant raised.
    """
    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    response = client.post(
        "/admin/people/student/add/",
        {
            "admission_number": "KC/002",
            "first_name": "Bisi",
            "last_name": "Adeyemi",
            "status": "ACTIVE",
            "gender": "F",
            "guardian_links-TOTAL_FORMS": "0",
            "guardian_links-INITIAL_FORMS": "0",
            "guardian_links-MIN_NUM_FORMS": "0",
            "guardian_links-MAX_NUM_FORMS": "1000",
        },
        HTTP_HOST=PLATFORM_HOST,
    )
    assert response.status_code in (200, 302), response.status_code

    with schema_context(school.schema_name):
        assert Student.objects.filter(admission_number="KC/002").exists()
    # There is no "and not in the platform database" half to assert: `people`
    # is tenant-only, so `default` has no `people_student` table for the row to
    # have landed in. The router is what makes that true, and it has its own
    # tests in test_isolation.py.

    # The history row, meanwhile, *is* in the platform database — with a
    # content type from there, which is the part that used to fail.
    from django.contrib.admin.models import LogEntry

    entry = LogEntry.objects.latest("action_time")
    assert entry.content_type.model_class() is Student


def test_the_admin_history_page_still_renders_inside_a_tenant(client, superuser, school):
    with schema_context(school.schema_name):
        student = Student.objects.create(
            admission_number="KC/003", first_name="Chidi", last_name="Nwosu"
        )

    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    response = client.get(f"/admin/people/student/{student.pk}/history/", HTTP_HOST=PLATFORM_HOST)
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Accountability
# ---------------------------------------------------------------------------
def test_entering_and_leaving_are_audited_on_the_platform(client, superuser, school):
    _login(client, superuser, "platform-pass")

    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)
    client.post("/admin/switch-tenant/", {"tenant": ""}, HTTP_HOST=PLATFORM_HOST)

    entered = AuditLog.objects.get(action=AuditAction.TENANT_ENTERED)
    exited = AuditLog.objects.get(action=AuditAction.TENANT_EXITED)

    # Named by tenant on a *platform* row: the school's own database has no
    # row for an act done to it from outside.
    assert entered.tenant_slug == school.slug
    assert exited.tenant_slug == school.slug
    assert entered.actor_id == superuser.pk

    with schema_context(school.schema_name):
        assert not AuditLog.objects.filter(action=AuditAction.TENANT_ENTERED).exists()


def test_api_traffic_is_untouched_by_an_admin_selection(client, superuser, school):
    """The selection is scoped to `/admin/`. An API request on the platform
    host must not inherit it — that is a tenant chosen by a cookie, which is
    exactly what `TenantResolutionMiddleware` refuses to allow anywhere else.
    """
    _login(client, superuser, "platform-pass")
    client.post("/admin/switch-tenant/", {"tenant": school.slug}, HTTP_HOST=PLATFORM_HOST)

    response = client.get("/api/v1/me/", HTTP_HOST=PLATFORM_HOST)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"
