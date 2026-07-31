"""Deleting reference data does not take an academic year with it.

Every academic-structure viewset is a plain `ModelViewSet`, so `DELETE
/academics/sessions/{id}/` was routed — and `Term.session`, `Enrolment.session`
and `FeeStructure.session` are all CASCADE, as is everything under a term.
One request removed the terms, the enrolments, every subject registration,
every score, every subject and term result, and the register.

Where an invoice happened to exist the same request raised `ProtectedError`
instead (`Invoice.session` is PROTECT) — an unhandled 500 on the identical
action, decided by whether the bursar had run invoicing yet.
"""

from decimal import Decimal

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Enrolment,
    Subject,
    SubjectRegistration,
    Term,
)
from apps.academics.services import enrol_student, register_subjects
from apps.accounts.models import Role, User
from apps.assessment.models import AssessmentComponent
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def headers(school):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def year(school):
    """One session with a term, a class, a pupil, an enrolment and a mark."""
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
            first_name="Ada",
            last_name="Obi",
            current_level=level,
            current_arm=arm,
        )
        enrolment = enrol_student(student=student, session=session, level=level, class_arm=arm)
        register_subjects(enrolment=enrolment, term=term, subjects=[maths])
        yield {
            "session": session,
            "term": term,
            "level": level,
            "arm": arm,
            "subject": maths,
            "enrolment": enrolment,
        }


def _delete(client, headers, path, pk):
    return client.delete(f"/api/v1/academics/{path}/{pk}/", **headers)


def test_a_session_with_a_year_behind_it_is_not_deletable(client, school, headers, year):
    response = _delete(client, headers, "sessions", year["session"].pk)

    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "DEPENDENTS_EXIST"
    # The message names what is in the way rather than just refusing.
    assert body["details"]["dependents"], body

    with schema_context(school.schema_name):
        assert AcademicSession.objects.filter(pk=year["session"].pk).exists()
        assert Term.objects.exists()
        assert Enrolment.objects.exists()
        assert SubjectRegistration.objects.exists()


def test_a_term_with_registrations_is_not_deletable(client, school, headers, year):
    assert _delete(client, headers, "terms", year["term"].pk).status_code == 409
    with schema_context(school.schema_name):
        assert SubjectRegistration.objects.exists()


def test_an_enrolment_with_registrations_is_not_deletable(client, school, headers, year):
    response = client.delete(f"/api/v1/academics/enrolments/{year['enrolment'].pk}/", **headers)
    assert response.status_code == 409
    with schema_context(school.schema_name):
        assert SubjectRegistration.objects.exists()


def test_an_unused_session_still_deletes(client, school, headers, year):
    """The guard is about dependents, not about sessions. A year created by
    mistake this morning is still a mistake to be tidied away."""
    with schema_context(school.schema_name):
        spare = AcademicSession.objects.create(
            name="2027/2028", start_date="2027-09-01", end_date="2028-07-31"
        )

    assert _delete(client, headers, "sessions", spare.pk).status_code == 204
    with schema_context(school.schema_name):
        assert not AcademicSession.objects.filter(pk=spare.pk).exists()


def test_a_protected_dependent_is_a_409_not_a_500(client, school, headers, year):
    """`Invoice.session` is PROTECT, so this path used to raise ProtectedError
    and reach DRF unhandled. Same action, same answer, whether the bursar has
    billed yet or not."""
    from apps.finance.models import FeeItem, FeeStructure
    from apps.finance.services import generate_invoices

    with schema_context(school.schema_name):
        structure = FeeStructure.objects.create(
            name="JSS1 First Term", session=year["session"], term=year["term"], level=year["level"]
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))
        generate_invoices(structure=structure)

    response = _delete(client, headers, "sessions", year["session"].pk)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DEPENDENTS_EXIST"


def test_deleting_a_marked_component_does_not_wipe_the_marks(client, school, headers, year):
    """`Score.component` is CASCADE: removing the "Exam" column removed every
    exam mark entered under it."""
    from apps.assessment.models import Score
    from apps.assessment.services import ScoreRow, enter_scores

    with schema_context(school.schema_name):
        component = AssessmentComponent.objects.create(
            code="EXAM", name="Exam", subject=year["subject"], max_score=Decimal("60.00")
        )
        enter_scores(
            term=year["term"],
            subject=year["subject"],
            component=component,
            rows=[ScoreRow(student_id=str(year["enrolment"].student_id), score=Decimal("55"))],
        )
        assert Score.objects.count() == 1

    response = client.delete(f"/api/v1/assessment/components/{component.pk}/", **headers)
    assert response.status_code == 409
    with schema_context(school.schema_name):
        assert Score.objects.count() == 1


def test_the_stream_link_being_nulled_is_not_a_dependent(client, school, headers, year):
    """`ClassArm.stream` is SET_NULL. Nulling it loses nothing, so it must not
    block the delete — or half the reference data becomes undeletable."""
    from apps.academics.models import Stream

    with schema_context(school.schema_name):
        stream = Stream.objects.create(code="SCI", name="Science")
        arm = ClassArm.objects.get(pk=year["arm"].pk)
        arm.stream = stream
        arm.save(update_fields=["stream", "updated_at"])

    assert _delete(client, headers, "streams", stream.pk).status_code == 204
    with schema_context(school.schema_name):
        arm.refresh_from_db()
        assert arm.stream_id is None
        assert ClassArm.objects.filter(pk=arm.pk).exists()
