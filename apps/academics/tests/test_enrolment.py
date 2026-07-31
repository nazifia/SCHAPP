import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment
from apps.academics.services import ArmFull, enrol_student
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def setup(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm = ClassArm.objects.create(level=level, name="A", capacity=2)
        yield {"session": session, "level": level, "arm": arm}


def _student(n: int) -> Student:
    return Student.objects.create(
        admission_number=f"KC/25/{n:04d}", first_name=f"Pupil{n}", last_name="Test"
    )


def test_enrolling_updates_the_students_current_position(school, setup):
    with schema_context(school.schema_name):
        student = _student(1)
        enrol_student(
            student=student, session=setup["session"], level=setup["level"], class_arm=setup["arm"]
        )
        student.refresh_from_db()
        assert student.current_level == setup["level"]
        assert student.current_arm == setup["arm"]


def test_enrolling_twice_in_one_session_returns_the_same_row(school, setup):
    with schema_context(school.schema_name):
        student = _student(1)
        first = enrol_student(student=student, session=setup["session"], level=setup["level"])
        second = enrol_student(student=student, session=setup["session"], level=setup["level"])
        assert first.pk == second.pk
        assert Enrolment.objects.count() == 1


def test_a_full_arm_refuses_another_student(school, setup):
    with schema_context(school.schema_name):
        for n in range(1, 3):
            enrol_student(
                student=_student(n),
                session=setup["session"],
                level=setup["level"],
                class_arm=setup["arm"],
            )
        with pytest.raises(ArmFull) as exc:
            enrol_student(
                student=_student(3),
                session=setup["session"],
                level=setup["level"],
                class_arm=setup["arm"],
            )
        assert exc.value.details == {"capacity": 2, "enrolled": 2}


def test_capacity_can_be_overridden_for_a_bulk_import(school, setup):
    with schema_context(school.schema_name):
        for n in range(1, 4):
            enrol_student(
                student=_student(n),
                session=setup["session"],
                level=setup["level"],
                class_arm=setup["arm"],
                enforce_capacity=False,
            )
        assert Enrolment.objects.count() == 3


def test_only_one_session_is_current_at_a_time(school, setup):
    with schema_context(school.schema_name):
        newer = AcademicSession.objects.create(
            name="2026/2027", start_date="2026-09-01", end_date="2027-07-31", is_current=True
        )
        setup["session"].refresh_from_db()
        assert newer.is_current
        assert not setup["session"].is_current
