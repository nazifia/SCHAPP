"""Reports scoped by query string answer the right status.

The scope of a report is a query parameter, not a path segment, and every
endpoint resolved it by hand. Three mistakes came back wrong: a missing
parameter was reported as 404 "no such term", a malformed id escaped the ORM
as an unhandled `django.core.exceptions.ValidationError` — a 500 on a typo,
and mid-download on the streaming exports — and an id matching nothing
dropped the filter, widening the report to everything.
"""

import uuid

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Subject, Term
from apps.accounts.models import Role, User
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

GHOST = str(uuid.uuid4())


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def admin(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        yield user


@pytest.fixture
def headers(school, admin):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(admin, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def scope(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        term = Term.objects.create(
            session=session,
            index=1,
            name="First Term",
            start_date="2025-09-01",
            end_date="2025-12-15",
            is_current=True,
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm = ClassArm.objects.create(level=level, name="A")
        Subject.objects.create(code="MTH", title="Mathematics")
        Student.objects.create(
            admission_number="KC/25/0001",
            first_name="Ngozi",
            last_name="Ali",
            current_level=level,
            current_arm=arm,
        )
        yield {"session": session, "term": term, "arm": arm}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # A parameter the endpoint cannot work without.
        ("/api/v1/assessment/broadsheet/", 400),
        ("/api/v1/assessment/scores/sheet/", 400),
        # An id that is not an id.
        ("/api/v1/assessment/broadsheet/?term=banana", 400),
        ("/api/v1/reports/overview/?term=banana", 400),
        ("/api/v1/finance/reports/?session=banana", 400),
        ("/api/v1/finance/reports/defaulters/?term=banana", 400),
        ("/api/v1/people/students/id-cards/?current_arm=banana", 400),
        # Streaming exports: the old failure landed inside the response body.
        ("/api/v1/reports/export/results/?term=banana", 400),
        ("/api/v1/reports/export/invoices/?term=banana", 400),
        ("/api/v1/reports/export/attendance/?date_from=banana", 400),
        # A well-formed id matching nothing keeps 404 — and must not silently
        # widen the report to every term.
        (f"/api/v1/assessment/broadsheet/?term={GHOST}", 404),
        (f"/api/v1/reports/overview/?session={GHOST}", 404),
        (f"/api/v1/reports/export/results/?term={GHOST}", 404),
    ],
)
def test_a_bad_scope_is_rejected_not_crashed(client, headers, scope, url, expected):
    response = client.get(url, **headers)
    if hasattr(response, "streaming_content"):  # consume, or nothing is evaluated
        b"".join(response.streaming_content)
    assert response.status_code == expected


def test_a_good_scope_still_works(client, headers, scope):
    term, arm = scope["term"], scope["arm"]

    assert (
        client.get(f"/api/v1/assessment/broadsheet/?term={term.pk}", **headers).status_code == 200
    )
    assert client.get("/api/v1/reports/overview/", **headers).status_code == 200
    assert (
        client.get(f"/api/v1/people/students/id-cards/?current_arm={arm.pk}", **headers).status_code
        == 200
    )

    export = client.get("/api/v1/reports/export/attendance/?date_from=2025-09-01", **headers)
    assert export.status_code == 200
    assert b"".join(export.streaming_content).startswith(b"Date,")
