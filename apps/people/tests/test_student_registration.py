"""Registering one pupil from the office screen puts them on the roll.

The CSV import and the admissions conversion both create the `Enrolment`;
`POST /people/students/` — the "Add student" form, which is how the child who
turns up in week three gets entered — created only the `Student`. The record
carried a `current_arm`, so the office saw the class it had typed, while the
class list, the promotion engine, the capacity count and next term's invoice
run all read the enrolment and saw nobody.
"""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment, EnrolmentStatus
from apps.accounts.models import Role, User
from apps.auth_phone import tokens
from apps.people.models import Student, StudentStatus
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def world(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm = ClassArm.objects.create(level=level, name="A", capacity=1)
        admin = User.objects.create_user("+2348031111111", first_name="Registrar")
        admin.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(admin, tenant=school)
        yield {"session": session, "level": level, "arm": arm, "access": pair["access"]}


def _register(client, school, world, **over):
    body = {
        "first_name": "Ada",
        "last_name": "Obi",
        "current_level": str(world["level"].pk),
        "current_arm": str(world["arm"].pk),
        "status": StudentStatus.ACTIVE,
    }
    body.update(over)
    return client.post(
        "/api/v1/people/students/",
        body,
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {world['access']}",
    )


def test_the_add_form_enrols_the_pupil(client, school, world):
    response = _register(client, school, world)
    assert response.status_code == 201, response.content

    with schema_context(school.schema_name):
        student = Student.objects.get(pk=response.json()["id"])
        enrolment = Enrolment.objects.get(student=student)
        assert enrolment.session == world["session"]
        assert enrolment.class_arm == world["arm"]
        assert enrolment.status == EnrolmentStatus.ACTIVE
        # The admission number is still allocated by the service, not typed.
        assert student.admission_number


def test_an_admitted_pupil_is_not_yet_on_the_roll(client, school, world):
    """ADMITTED means "admitted, not yet enrolled". Enrolling one here would
    fill a seat and raise an invoice for a child who has not turned up."""
    response = _register(client, school, world, status=StudentStatus.ADMITTED)
    assert response.status_code == 201, response.content

    with schema_context(school.schema_name):
        assert not Enrolment.objects.filter(student_id=response.json()["id"]).exists()


def test_a_pupil_with_no_class_is_created_without_an_enrolment(client, school, world):
    """A record entered before the class is decided is legitimate — there is
    simply no cohort to place them in yet."""
    response = _register(client, school, world, current_level=None, current_arm=None)
    assert response.status_code == 201, response.content

    with schema_context(school.schema_name):
        assert not Enrolment.objects.filter(student_id=response.json()["id"]).exists()


def test_a_full_class_refuses_the_registration_outright(client, school, world):
    """Capacity is 1 and the first pupil takes it. The second must fail whole:
    a student row left behind by a half-done registration is a duplicate the
    registrar creates on their second, successful try."""
    assert _register(client, school, world).status_code == 201

    response = _register(client, school, world, first_name="Bola", last_name="Ade")
    assert response.status_code == 400, response.content
    assert response.json()["error"]["code"] == "ARM_FULL"

    with schema_context(school.schema_name):
        assert Student.objects.filter(first_name="Bola").count() == 0
