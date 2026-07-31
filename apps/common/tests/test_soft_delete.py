"""`DELETE` on a soft-delete model does not remove the row.

`SoftDeleteModel` opens by saying these are "rows schools must never truly
lose", and `Student` repeats it. Nothing enforced either: `soft_delete()` was
written in Phase 1 and no application code ever called it, while
`StudentViewSet` and `StaffViewSet` were plain `ModelViewSet`s. `DELETE
/people/students/{id}/` reached `ModelViewSet.destroy`, which calls
`instance.delete()` — a real delete, taking every enrolment, attendance
record, registration, result and guardian link with it by cascade.
"""

import pytest

from apps.accounts.models import Role, User
from apps.people.models import Staff, Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def headers(school):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Registrar")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def pupil(school):
    with schema_context(school.schema_name):
        yield Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Obi"
        )


def test_deleting_a_student_over_the_api_keeps_the_row(client, school, headers, pupil):
    response = client.delete(f"/api/v1/people/students/{pupil.pk}/", **headers)
    assert response.status_code == 204

    with schema_context(school.schema_name):
        # Gone from every list, still on disk.
        assert not Student.objects.alive().filter(pk=pupil.pk).exists()
        row = Student.objects.get(pk=pupil.pk)
        assert row.deleted_at is not None


def test_the_academic_history_survives(client, school, headers, pupil):
    """The cascade was the real damage: a hard delete took the enrolment, the
    register and the results with it."""
    from apps.academics.models import AcademicSession, ClassLevel, Enrolment
    from apps.academics.services import enrol_student

    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        enrol_student(student=pupil, session=session, level=level)

    client.delete(f"/api/v1/people/students/{pupil.pk}/", **headers)

    with schema_context(school.schema_name):
        assert Enrolment.objects.filter(student=pupil).exists()


def test_a_student_holding_an_invoice_is_not_a_500(client, school, headers, pupil):
    """`Invoice.student` is `on_delete=PROTECT`, so the old hard delete raised
    `ProtectedError` — an unhandled 500 for a routine request."""
    from decimal import Decimal

    from apps.academics.models import AcademicSession, ClassLevel
    from apps.academics.services import enrol_student
    from apps.finance.models import FeeItem, FeeStructure
    from apps.finance.services import generate_invoices

    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        enrol_student(student=pupil, session=session, level=level)
        structure = FeeStructure.objects.create(name="Fees", session=session, level=level)
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))
        generate_invoices(structure=structure)

    assert client.delete(f"/api/v1/people/students/{pupil.pk}/", **headers).status_code == 204


def test_deleting_a_staff_record_keeps_it_too(client, school, headers):
    with schema_context(school.schema_name):
        teacher = Staff.objects.create(
            staff_number="STF/001", first_name="Ngozi", last_name="Okeke"
        )

    assert client.delete(f"/api/v1/people/staff/{teacher.pk}/", **headers).status_code == 204

    with schema_context(school.schema_name):
        assert not Staff.objects.alive().filter(pk=teacher.pk).exists()
        assert Staff.objects.get(pk=teacher.pk).deleted_at is not None


def test_a_queryset_delete_is_soft_too(school, pupil):
    """`Model.delete()` and `QuerySet.delete()` are different code paths in
    Django, and a bulk delete is the one that would take a whole class out."""
    with schema_context(school.schema_name):
        Student.objects.filter(pk=pupil.pk).delete()
        assert Student.objects.get(pk=pupil.pk).deleted_at is not None


def test_hard_delete_still_removes_the_row(school, pupil):
    """The deliberate way out: a data-protection erasure means it."""
    with schema_context(school.schema_name):
        pupil.hard_delete()
        assert not Student.objects.filter(pk=pupil.pk).exists()


def test_hard_delete_works_on_a_queryset_as_well(school, pupil):
    with schema_context(school.schema_name):
        Student.objects.filter(pk=pupil.pk).hard_delete()
        assert not Student.objects.filter(pk=pupil.pk).exists()


def test_deleting_twice_does_not_move_the_timestamp(school, pupil):
    """The first deletion is the one that happened."""
    with schema_context(school.schema_name):
        pupil.delete()
        first = Student.objects.get(pk=pupil.pk).deleted_at
        Student.objects.filter(pk=pupil.pk).delete()
        assert Student.objects.get(pk=pupil.pk).deleted_at == first
