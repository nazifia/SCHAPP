"""A pupil who leaves stops being on the roll.

`Student.status` was writable straight through the serializer with nothing
behind it, so marking a leaver WITHDRAWN or TRANSFERRED left their `Enrolment`
on ACTIVE. Four separate queries read an ACTIVE enrolment as "on the roll":
next term's invoice run, the promotion engine, the class-capacity count, and
the class list itself. `EnrolmentStatus.WITHDRAWN` and `TRANSFERRED` were
declared and no code ever wrote either.

Staff already had `set_staff_status` doing exactly this for the login. This is
the missing half for students.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment, EnrolmentStatus
from apps.academics.services import ArmFull, enrol_student
from apps.accounts.models import Role, User
from apps.people.models import Student, StudentStatus
from apps.people.services import set_student_status
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
        arm = ClassArm.objects.create(level=level, name="A", capacity=2)
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Obi", current_level=level
        )
        enrol_student(student=student, session=session, level=level, class_arm=arm)
        yield {"session": session, "level": level, "arm": arm, "student": student}


def _enrolment(student):
    return Enrolment.objects.get(student=student)


@pytest.mark.parametrize(
    ("student_status", "expected"),
    [
        (StudentStatus.WITHDRAWN, EnrolmentStatus.WITHDRAWN),
        (StudentStatus.TRANSFERRED, EnrolmentStatus.TRANSFERRED),
        (StudentStatus.GRADUATED, EnrolmentStatus.GRADUATED),
    ],
)
def test_leaving_closes_the_enrolment(school, world, student_status, expected):
    with schema_context(school.schema_name):
        student = set_student_status(world["student"], student_status)
        assert _enrolment(student).status == expected
        assert student.date_left is not None


def test_a_suspended_pupil_is_still_on_the_roll(school, world):
    """Suspension ends; the pupil keeps their place and the school keeps
    billing them. Closing the enrolment here would write off a term's fees."""
    with schema_context(school.schema_name):
        set_student_status(world["student"], StudentStatus.SUSPENDED)
        assert _enrolment(world["student"]).status == EnrolmentStatus.ACTIVE
        assert world["student"].date_left is None


def test_a_departed_pupil_is_not_billed_for_next_term(school, world):
    """`cohort_for` is `status=ACTIVE`, which is what made this a money bug."""
    from apps.finance.models import FeeItem, FeeStructure
    from apps.finance.services import cohort_for, generate_invoices

    with schema_context(school.schema_name):
        structure = FeeStructure.objects.create(
            name="JSS1 Second Term", session=world["session"], level=world["level"]
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))

        assert len(cohort_for(structure)) == 1
        set_student_status(world["student"], StudentStatus.TRANSFERRED)
        assert cohort_for(structure) == []
        assert generate_invoices(structure=structure).created == 0


def test_a_departed_pupil_frees_their_seat_in_the_class(school, world):
    with schema_context(school.schema_name):
        second = Student.objects.create(
            admission_number="KC/25/0002", first_name="Bola", last_name="Ade"
        )
        third = Student.objects.create(
            admission_number="KC/25/0003", first_name="Chidi", last_name="Eze"
        )
        enrol_student(
            student=second, session=world["session"], level=world["level"], class_arm=world["arm"]
        )
        # Capacity is 2 and both seats are taken.
        with pytest.raises(ArmFull):
            enrol_student(
                student=third,
                session=world["session"],
                level=world["level"],
                class_arm=world["arm"],
            )

        set_student_status(world["student"], StudentStatus.WITHDRAWN)
        enrol_student(
            student=third, session=world["session"], level=world["level"], class_arm=world["arm"]
        )


def test_a_departed_pupil_is_skipped_by_the_promotion_engine(school, world):
    from apps.assessment.services import decide_promotions

    with schema_context(school.schema_name):
        set_student_status(world["student"], StudentStatus.TRANSFERRED)
        assert decide_promotions(session=world["session"]) == []


def test_reinstating_reopens_the_enrolment(school, world):
    with schema_context(school.schema_name):
        set_student_status(world["student"], StudentStatus.WITHDRAWN)
        student = set_student_status(world["student"], StudentStatus.ACTIVE)

        assert _enrolment(student).status == EnrolmentStatus.ACTIVE
        assert student.date_left is None


def test_a_graduate_is_not_reopened_by_flipping_the_status(school, world):
    """Someone coming back after graduating is a fresh admission, not a
    reinstatement — the same reasoning that stops `set_staff_status` restoring
    roles automatically."""
    with schema_context(school.schema_name):
        set_student_status(world["student"], StudentStatus.GRADUATED)
        set_student_status(world["student"], StudentStatus.ACTIVE)
        assert _enrolment(world["student"]).status == EnrolmentStatus.GRADUATED


def test_past_sessions_are_not_rewritten(school, world):
    """PROMOTED and REPEATED rows are the history of past sessions. A pupil
    leaving in SSS2 must not retroactively un-promote them out of JSS1."""
    with schema_context(school.schema_name):
        older = AcademicSession.objects.create(
            name="2024/2025", start_date="2024-09-01", end_date="2025-07-31"
        )
        history = Enrolment.objects.create(
            student=world["student"],
            session=older,
            level=world["level"],
            status=EnrolmentStatus.PROMOTED,
        )
        set_student_status(world["student"], StudentStatus.WITHDRAWN)
        history.refresh_from_db()
        assert history.status == EnrolmentStatus.PROMOTED


def test_the_edit_form_goes_through_the_service(client, school, world):
    """The bug's actual entry point: `status` on the ordinary PATCH."""
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        admin = User.objects.create_user("+2348031111111", first_name="Registrar")
        admin.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(admin, tenant=school)

    response = client.patch(
        f"/api/v1/people/students/{world['student'].pk}/",
        {"status": StudentStatus.TRANSFERRED},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )
    assert response.status_code == 200, response.content

    with schema_context(school.schema_name):
        assert _enrolment(world["student"]).status == EnrolmentStatus.TRANSFERRED


def test_reinstatement_reopens_only_the_current_session(school, world):
    """A pupil who has left twice comes back once.

    Reinstatement was unscoped, so it reinstated *every* enrolment the pupil
    had ever left. A child who withdrew in a past session, returned, and
    withdrew again was put back into both cohorts at once — and an ACTIVE
    enrolment in a closed session is exactly what next term's invoice run and
    the promotion engine read as "on the roll".
    """
    with schema_context(school.schema_name):
        student = world["student"]
        old_session = AcademicSession.objects.create(
            name="2023/2024", start_date="2023-09-01", end_date="2024-07-31"
        )
        old = Enrolment.objects.create(
            student=student,
            session=old_session,
            level=world["level"],
            status=EnrolmentStatus.WITHDRAWN,
        )

        set_student_status(student, StudentStatus.WITHDRAWN)
        set_student_status(student, StudentStatus.ACTIVE)

        current = Enrolment.objects.get(student=student, session=world["session"])
        assert current.status == EnrolmentStatus.ACTIVE
        old.refresh_from_db()
        assert old.status == EnrolmentStatus.WITHDRAWN
