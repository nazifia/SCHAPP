"""Placing students in classes, after they are already enrolled.

`apply_promotions` creates next session's enrolments with `class_arm=None` on
purpose, so this is the step every promoted year group has to go through.
"""

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    EnrolmentStatus,
    Stream,
)
from apps.academics.selectors import class_list, unplaced_enrolments, with_occupancy
from apps.academics.services import (
    ArmFull,
    ArmInactive,
    ArmMismatch,
    EnrolmentNotActive,
    allocate_to_arm,
    assign_to_arm,
    enrol_student,
)
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("queens-college")


@pytest.fixture
def setup(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        other_level = ClassLevel.objects.get(code="JSS2")
        yield {
            "session": session,
            "level": level,
            "other_level": other_level,
            "gold": ClassArm.objects.create(level=level, name="Gold", capacity=2),
            "silver": ClassArm.objects.create(level=level, name="Silver", capacity=2),
        }


def _enrol(setup, n: int, **kwargs):
    student = Student.objects.create(
        admission_number=f"QC/25/{n:04d}", first_name=f"Pupil{n}", last_name="Test"
    )
    return enrol_student(student=student, session=setup["session"], level=setup["level"], **kwargs)


def test_assigning_seats_the_student_and_syncs_their_position(school, setup):
    with schema_context(school.schema_name):
        enrolment = _enrol(setup, 1)
        assert enrolment.class_arm is None

        assign_to_arm(enrolment=enrolment, class_arm=setup["gold"])

        enrolment.refresh_from_db()
        assert enrolment.class_arm == setup["gold"]
        assert enrolment.roll_number == 1
        enrolment.student.refresh_from_db()
        assert enrolment.student.current_arm == setup["gold"]


def test_transfer_moves_the_student_and_renumbers_them(school, setup):
    with schema_context(school.schema_name):
        first = _enrol(setup, 1)
        second = _enrol(setup, 2)
        assign_to_arm(enrolment=first, class_arm=setup["gold"])
        assign_to_arm(enrolment=second, class_arm=setup["silver"])
        assert second.roll_number == 1  # first in its own class, not second overall

        assign_to_arm(enrolment=second, class_arm=setup["gold"])

        second.refresh_from_db()
        assert second.class_arm == setup["gold"]
        assert second.roll_number == 2
        second.student.refresh_from_db()
        assert second.student.current_arm == setup["gold"]
        assert class_list(session=setup["session"], class_arm=setup["silver"]).count() == 0


def test_a_full_class_refuses_a_transfer(school, setup):
    with schema_context(school.schema_name):
        for n in (1, 2):
            assign_to_arm(enrolment=_enrol(setup, n), class_arm=setup["gold"])
        with pytest.raises(ArmFull) as exc:
            assign_to_arm(enrolment=_enrol(setup, 3), class_arm=setup["gold"])
        assert exc.value.details == {"capacity": 2, "enrolled": 2}


def test_an_arm_from_another_level_is_refused(school, setup):
    with schema_context(school.schema_name):
        wrong = ClassArm.objects.create(level=setup["other_level"], name="A")
        with pytest.raises(ArmMismatch):
            assign_to_arm(enrolment=_enrol(setup, 1), class_arm=wrong)


def test_a_retired_arm_is_refused(school, setup):
    with schema_context(school.schema_name):
        setup["gold"].is_active = False
        setup["gold"].save(update_fields=["is_active"])
        with pytest.raises(ArmInactive):
            assign_to_arm(enrolment=_enrol(setup, 1), class_arm=setup["gold"])


def test_a_leaver_cannot_be_placed(school, setup):
    with schema_context(school.schema_name):
        enrolment = _enrol(setup, 1)
        enrolment.status = EnrolmentStatus.WITHDRAWN
        enrolment.save(update_fields=["status"])
        with pytest.raises(EnrolmentNotActive):
            assign_to_arm(enrolment=enrolment, class_arm=setup["gold"])


def test_a_streamed_class_refuses_the_wrong_stream_and_sets_an_unset_one(school, setup):
    with schema_context(school.schema_name):
        science, _ = Stream.objects.get_or_create(code="science", defaults={"name": "Science"})
        arts, _ = Stream.objects.get_or_create(code="arts", defaults={"name": "Arts"})
        setup["gold"].stream = science
        setup["gold"].save(update_fields=["stream"])

        undecided = _enrol(setup, 1)
        assign_to_arm(enrolment=undecided, class_arm=setup["gold"])
        undecided.student.refresh_from_db()
        assert undecided.student.stream == science  # placement is the choice

        artist = _enrol(setup, 2)
        artist.student.stream = arts
        artist.student.save(update_fields=["stream"])
        with pytest.raises(ArmMismatch) as exc:
            assign_to_arm(enrolment=artist, class_arm=setup["gold"])
        assert exc.value.code == "STREAM_MISMATCH"


def test_a_batch_too_big_for_the_class_writes_nothing(school, setup):
    with schema_context(school.schema_name):
        batch = [_enrol(setup, n) for n in (1, 2, 3)]
        with pytest.raises(ArmFull):
            allocate_to_arm(enrolments=batch, class_arm=setup["gold"])
        assert unplaced_enrolments(session=setup["session"]).count() == 3


def test_a_batch_that_fits_is_seated_and_numbered_in_order(school, setup):
    with schema_context(school.schema_name):
        batch = [_enrol(setup, n) for n in (1, 2)]
        allocate_to_arm(enrolments=batch, class_arm=setup["gold"])

        seated = class_list(session=setup["session"], class_arm=setup["gold"])
        assert [e.roll_number for e in seated] == [1, 2]
        assert not unplaced_enrolments(session=setup["session"]).exists()

        arm = with_occupancy(ClassArm.objects.filter(pk=setup["gold"].pk), setup["session"]).get()
        assert arm.enrolled == 2


def test_the_class_list_reads_in_roll_number_order(school, setup):
    with schema_context(school.schema_name):
        # Alphabetically Aardvark comes first; on the register they are number 2.
        first = _enrol(setup, 1)
        second = _enrol(setup, 2)
        second.student.last_name = "Aardvark"
        second.student.save(update_fields=["last_name"])

        assign_to_arm(enrolment=first, class_arm=setup["gold"])
        assign_to_arm(enrolment=second, class_arm=setup["gold"])

        seated = list(class_list(session=setup["session"], class_arm=setup["gold"]))
        assert [e.pk for e in seated] == [first.pk, second.pk]
