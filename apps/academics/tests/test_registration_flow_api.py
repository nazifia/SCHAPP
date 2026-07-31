"""The course-registration screen's flow, over HTTP.

Every step the client makes: pick a term, search the register for a student,
find the enrolment that joins them, list the catalogue, post the set, and read
back what is already on the term. The screen was written against these six
calls and none of them had a test.
"""

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassLevel,
    Department,
    Faculty,
    Programme,
    Stream,
    Subject,
    Term,
)
from apps.academics.services import enrol_student
from apps.accounts.models import Role, User
from apps.auth_phone import tokens
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def poly(make_tenant, ncc_table):
    return make_tenant("flow-poly", institution_type="TERTIARY")


@pytest.fixture
def flow(poly):
    with schema_context(poly.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        term = Term.objects.create(
            session=session,
            index=1,
            name="First Semester",
            start_date="2025-09-01",
            end_date="2026-01-31",
            is_current=True,
        )
        faculty = Faculty.objects.create(code="SCI", name="Science")
        dept = Department.objects.create(faculty=faculty, code="CSC", name="Computer Science")
        programme = Programme.objects.create(
            department=dept,
            code="ND-CSC",
            name="Computer Science",
            min_credit_units=15,
            max_credit_units=24,
        )
        level = ClassLevel.objects.get(code="100")
        student = Student.objects.create(
            admission_number="CSC/25/0001",
            first_name="Chidi",
            last_name="Eze",
            programme=programme,
            current_level=level,
        )
        enrolment = enrol_student(
            student=student, session=session, level=level, programme=programme
        )
        subjects = [
            Subject.objects.create(code=f"CSC10{n}", title=f"Course {n}", credit_units=6)
            for n in range(1, 3)
        ]
        user = User.objects.create_user("+2348032222222", first_name="Registry")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=poly)
        yield {
            "headers": {
                "HTTP_X_TENANT_SLUG": poly.slug,
                "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
            },
            "session": session,
            "term": term,
            "student": student,
            "enrolment": enrolment,
            "subjects": subjects,
        }


def _rows(response):
    data = response.json()
    return data["results"] if isinstance(data, dict) and "results" in data else data


def test_the_catalogue_narrows_to_the_student(client, flow):
    """Listing every active course in the school made the screen a haystack,
    and every rule below is one the server was already going to enforce after
    the tick — one round trip too late to be useful."""
    student_level = flow["enrolment"].level
    higher = ClassLevel.objects.filter(order__gt=student_level.order).order_by("order").first()
    lower = ClassLevel.objects.filter(order__lt=student_level.order).order_by("-order").first()
    own_department = flow["enrolment"].programme.department
    other_department = Department.objects.create(
        faculty=own_department.faculty, code="MTH", name="Mathematics"
    )
    stream = Stream.objects.create(code="SCI", name="Science")

    kept = {
        "CSC101",  # from the fixture: no level, no department, no semester
        Subject.objects.create(
            code="CSC201", title="Own level", level=student_level, department=own_department
        ).code,
        # A carryover is by definition a course from a level already left.
        Subject.objects.create(code="CSC001", title="Carryover", level=lower).code,
        Subject.objects.create(code="GNS101", title="General studies", department=None).code,
        Subject.objects.create(code="CSC111", title="This semester", semester_offered=1).code,
    }
    dropped = {
        Subject.objects.create(code="CSC301", title="Next year", level=higher).code,
        Subject.objects.create(
            code="MTH201", title="Other department", department=other_department
        ).code,
        Subject.objects.create(code="CSC122", title="Next semester", semester_offered=2).code,
        Subject.objects.create(code="BIO101", title="Streamed", stream=stream).code,
        Subject.objects.create(code="CSC199", title="Withdrawn", is_active=False).code,
    }

    listed = {
        row["code"]
        for row in _rows(
            client.get(
                f"/api/v1/academics/subjects/?for_enrolment={flow['enrolment'].pk}"
                f"&term={flow['term'].pk}&page_size=200",
                **flow["headers"],
            )
        )
    }
    assert kept <= listed
    assert not (dropped & listed)


def test_the_registration_screen_can_walk_its_own_flow(client, flow):
    headers = flow["headers"]

    # 1. The term picker.
    terms = _rows(client.get("/api/v1/academics/terms/?page_size=20", **headers))
    term = next(t for t in terms if t["id"] == str(flow["term"].pk))
    assert term["accepts_registration"] is True
    assert term["session"] == str(flow["session"].pk)

    # 2. The student picker searches the register.
    students = _rows(client.get("/api/v1/people/students/?status=ACTIVE&search=Chidi", **headers))
    assert [s["id"] for s in students] == [str(flow["student"].pk)]

    # 3. The enrolment that joins the pupil to the term's session.
    enrolments = _rows(
        client.get(
            f"/api/v1/academics/enrolments/?student={flow['student'].pk}"
            f"&session={flow['session'].pk}",
            **headers,
        )
    )
    assert [e["id"] for e in enrolments] == [str(flow["enrolment"].pk)]

    # 4. The catalogue, narrowed to this student and searched.
    catalogue = _rows(
        client.get(
            f"/api/v1/academics/subjects/?is_active=true&search=CSC10"
            f"&for_enrolment={flow['enrolment'].pk}&term={flow['term'].pk}",
            **headers,
        )
    )
    assert {s["code"] for s in catalogue} == {"CSC101", "CSC102"}

    # 5. Register the ticked set.
    posted = client.post(
        "/api/v1/academics/registrations/register/",
        {
            "enrolment": str(flow["enrolment"].pk),
            "term": str(flow["term"].pk),
            "subjects": [str(s.pk) for s in flow["subjects"]],
            "submit": True,
        },
        content_type="application/json",
        **headers,
    )
    assert posted.status_code == 201, posted.content
    written = posted.json()
    assert {row["status"] for row in written} == {"SUBMITTED"}
    # The queue screen reads this to say whose course it is refusing.
    assert all(row["subject_name"] for row in written), written

    # 6. Read the term back: the screen greys out what is already on it.
    existing = _rows(
        client.get(
            f"/api/v1/academics/registrations/?enrolment={flow['enrolment'].pk}"
            f"&term={flow['term'].pk}",
            **headers,
        )
    )
    assert {row["subject"] for row in existing} == {str(s.pk) for s in flow["subjects"]}
