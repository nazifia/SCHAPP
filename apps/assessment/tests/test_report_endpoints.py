"""The transcript and broadsheet endpoints, over HTTP.

`test_documents.py` renders those templates directly, which proved the layout
and nothing about the URL in front of it. Both endpoints were in fact broken:
the transcript answered 500 because its context carried model instances the
JSON renderer cannot encode, and `?format=pdf` answered 404 on both because
DRF resolves `?format=` against the renderer list before the view is ever
called. Neither failure could show up in a template test.
"""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Subject, Term
from apps.academics.services import enrol_student, register_subjects
from apps.accounts.models import Role, User
from apps.assessment.models import AssessmentComponent
from apps.assessment.services import ScoreRow, enter_scores, recompute_term
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

BROADSHEET = "/api/v1/assessment/broadsheet/"


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
def marked_term(school):
    """One student with a full set of marks and a computed result."""
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
        maths = Subject.objects.create(code="MTH", title="Mathematics")
        student = Student.objects.create(
            admission_number="KC/25/0001",
            first_name="Ngozi",
            last_name="Ali",
            current_level=level,
            current_arm=arm,
        )
        register_subjects(
            enrolment=enrol_student(student=student, session=session, level=level, class_arm=arm),
            term=term,
            subjects=[maths],
        )
        components = {c.code: c for c in AssessmentComponent.objects.all()}
        for code, score in [("CA1", 18), ("CA2", 18), ("EXAM", 54)]:
            enter_scores(
                term=term,
                subject=maths,
                component=components[code],
                rows=[ScoreRow(student_id=str(student.pk), score=score)],
            )
        recompute_term(term)
        yield {"term": term, "arm": arm, "student": student}


def test_the_transcript_endpoint_returns_json(client, school, admin, marked_term):
    student = marked_term["student"]
    response = client.get(f"/api/v1/assessment/transcript/{student.pk}/", **_headers(school, admin))

    assert response.status_code == 200
    block = response.json()["terms"][0]
    # Names, not model instances — the 500 this test exists for.
    assert block["level"] == "JSS 1"
    assert block["programme"] is None
    assert block["subjects"][0]["code"] == "MTH"


def test_the_transcript_prints(client, school, admin, marked_term):
    response = client.get(
        f"/api/v1/assessment/transcript/{marked_term['student'].pk}/?format=pdf",
        **_headers(school, admin),
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_the_report_card_prints_for_a_client_asking_for_a_pdf(client, school, admin, marked_term):
    """The app sends `Accept: application/pdf`, and negotiation runs first.

    Without the PDF renderer on the viewset that header is answered 406 before
    the action runs at all — the endpoint works in a browser and fails from
    the phone, which is the only place it is used.
    """
    with schema_context(school.schema_name):
        from apps.assessment.models import TermResult

        term_result = TermResult.objects.get(term=marked_term["term"])

    response = client.get(
        f"/api/v1/assessment/term-results/{term_result.pk}/report_card/",
        HTTP_ACCEPT="application/pdf",
        **_headers(school, admin),
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_the_broadsheet_returns_json_and_prints(client, school, admin, marked_term):
    headers = _headers(school, admin)
    query = f"?term={marked_term['term'].pk}&class_arm={marked_term['arm'].pk}"

    as_json = client.get(BROADSHEET + query, **headers)
    assert as_json.status_code == 200
    assert as_json.json()["rows"][0]["full_name"] == "Ngozi Ali"

    as_pdf = client.get(f"{BROADSHEET}{query}&format=pdf", **headers)
    assert as_pdf.status_code == 200
    assert as_pdf.content.startswith(b"%PDF")
