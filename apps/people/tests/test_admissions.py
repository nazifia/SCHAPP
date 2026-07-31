"""Admissions pipeline and guardian matching."""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment
from apps.people.models import Application, ApplicationStatus, Guardian, Student
from apps.people.services import (
    InvalidTransition,
    convert_application_to_student,
    link_guardian,
    submit_application,
    transition_application,
)
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
        arm = ClassArm.objects.create(level=level, name="A")
        yield {"session": session, "level": level, "arm": arm}


def _apply(setup, **over):
    return submit_application(
        first_name="Chinedu",
        last_name="Okafor",
        session=setup["session"],
        level_applied=setup["level"],
        guardian_name="Ngozi Okafor",
        guardian_phone="08031234567",
        **over,
    )


def test_an_application_gets_an_unguessable_reference(school, setup):
    with schema_context(school.schema_name):
        first = _apply(setup)
        second = _apply(setup)
        assert first.reference != second.reference
        assert first.reference.startswith("APP-")
        assert first.status == ApplicationStatus.SUBMITTED


def test_the_happy_path_runs_submitted_to_enrolled(school, setup):
    with schema_context(school.schema_name):
        application = _apply(setup)
        transition_application(application, ApplicationStatus.SCREENING, score=68)
        transition_application(application, ApplicationStatus.OFFERED)
        transition_application(application, ApplicationStatus.ACCEPTED)

        student = convert_application_to_student(
            application, session=setup["session"], class_arm=setup["arm"]
        )

        assert student.admission_number
        assert student.status == "ACTIVE"
        assert application.status == ApplicationStatus.ENROLLED
        assert Enrolment.objects.filter(student=student).count() == 1


@pytest.mark.parametrize(
    ("start", "target"),
    [
        (ApplicationStatus.SUBMITTED, ApplicationStatus.ACCEPTED),  # skips screening + offer
        (ApplicationStatus.SUBMITTED, ApplicationStatus.ENROLLED),
        (ApplicationStatus.REJECTED, ApplicationStatus.OFFERED),
    ],
)
def test_illegal_transitions_are_refused(school, setup, start, target):
    with schema_context(school.schema_name):
        application = _apply(setup)
        Application.objects.filter(pk=application.pk).update(status=start)
        application.refresh_from_db()

        with pytest.raises(InvalidTransition):
            transition_application(application, target)


def test_only_an_accepted_offer_can_be_enrolled(school, setup):
    with schema_context(school.schema_name):
        application = _apply(setup)
        transition_application(application, ApplicationStatus.SCREENING)
        with pytest.raises(InvalidTransition):
            convert_application_to_student(application, session=setup["session"])


def test_conversion_is_idempotent(school, setup):
    with schema_context(school.schema_name):
        application = _apply(setup)
        transition_application(application, ApplicationStatus.SCREENING)
        transition_application(application, ApplicationStatus.OFFERED)
        transition_application(application, ApplicationStatus.ACCEPTED)

        first = convert_application_to_student(application, session=setup["session"])
        second = convert_application_to_student(application, session=setup["session"])

        assert first.pk == second.pk
        assert Student.objects.count() == 1


def test_conversion_creates_the_guardian_link(school, setup):
    with schema_context(school.schema_name):
        application = _apply(setup)
        transition_application(application, ApplicationStatus.SCREENING)
        transition_application(application, ApplicationStatus.OFFERED)
        transition_application(application, ApplicationStatus.ACCEPTED)
        student = convert_application_to_student(application, session=setup["session"])

        link = student.guardian_links.get()
        assert link.is_primary
        assert link.guardian.phone == "+2348031234567"


def test_one_guardian_for_several_children(school, setup):
    """The reason a parent gets one login, not one per child."""
    with schema_context(school.schema_name):
        first = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Okafor"
        )
        second = Student.objects.create(
            admission_number="KC/25/0002", first_name="Emeka", last_name="Okafor"
        )

        link_guardian(student=first, phone="08031234567", first_name="Ngozi", last_name="Okafor")
        link_guardian(student=second, phone="+234 803 123 4567", last_name="Okafor")

        assert Guardian.objects.count() == 1
        assert Guardian.objects.get().students.count() == 2


def test_relinking_the_same_guardian_is_idempotent(school, setup):
    with schema_context(school.schema_name):
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Okafor"
        )
        link_guardian(student=student, phone="08031234567")
        link_guardian(student=student, phone="08031234567")
        assert student.guardian_links.count() == 1


def test_only_one_primary_guardian_per_student(school, setup):
    with schema_context(school.schema_name):
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Okafor"
        )
        link_guardian(student=student, phone="08031234567", is_primary=True)
        link_guardian(student=student, phone="08039999999", is_primary=True)

        primaries = student.guardian_links.filter(is_primary=True)
        assert primaries.count() == 1
        assert primaries.get().guardian.phone == "+2348039999999"


def test_a_bad_guardian_number_is_rejected(school, setup):
    from apps.people.services import AdmissionError

    with schema_context(school.schema_name):
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Okafor"
        )
        with pytest.raises(AdmissionError):
            link_guardian(student=student, phone="0803")


def test_require_nin_is_a_switch_that_does_something(school, setup):
    """It was stored and read by nothing, which is worse than not offering it:
    an administrator ticks the box and believes the records are complete."""
    from apps.people.services import AdmissionError, create_staff, create_student
    from apps.tenants.models import TenantConfiguration

    TenantConfiguration.objects.filter(tenant=school).update(require_nin=True)
    school.refresh_from_db()

    with schema_context(school.schema_name):
        with pytest.raises(AdmissionError) as exc:
            create_student(tenant=school, first_name="Chinedu", last_name="Okafor")
        assert exc.value.code == "NIN_REQUIRED"

        with pytest.raises(AdmissionError):
            create_staff(tenant=school, first_name="Ada", last_name="Nwosu")

        # With one, both go through.
        student = create_student(
            tenant=school, first_name="Chinedu", last_name="Okafor", nin="12345678901"
        )
        assert student.pk
