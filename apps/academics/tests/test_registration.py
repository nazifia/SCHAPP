"""Course registration: credit limits, prerequisites, carry-overs, windows."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.academics.models import (
    AcademicSession,
    ClassLevel,
    Department,
    Faculty,
    Programme,
    RegistrationStatus,
    Subject,
    SubjectRegistration,
    Term,
)
from apps.academics.serializers import SubjectSerializer
from apps.academics.services import (
    AcademicError,
    CreditLimitExceeded,
    PrerequisiteCycle,
    RegistrationClosed,
    assert_minimum_credits,
    assert_no_prerequisite_cycle,
    drop_subject,
    enrol_student,
    register_subjects,
    registered_credit_units,
)
from apps.people.models import Staff, Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def poly(make_tenant, ncc_table):
    return make_tenant("unity-poly", institution_type="TERTIARY")


@pytest.fixture
def setup(poly):
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
            Subject.objects.create(code=f"CSC10{i}", title=f"Course {i}", credit_units=6)
            for i in range(1, 6)
        ]
        yield {
            "session": session,
            "term": term,
            "programme": programme,
            "level": level,
            "student": student,
            "enrolment": enrolment,
            "subjects": subjects,
            "dept": dept,
        }


def test_registration_within_the_credit_ceiling_succeeds(poly, setup):
    with schema_context(poly.schema_name):
        registrations = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"][:4]
        )
        assert len(registrations) == 4
        assert registered_credit_units(setup["enrolment"], setup["term"]) == 24


def test_exceeding_the_maximum_credit_units_is_refused(poly, setup):
    with schema_context(poly.schema_name), pytest.raises(CreditLimitExceeded) as exc:
        register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"]
        )
    assert exc.value.details["proposed"] == 30
    assert exc.value.details["maximum"] == 24


def test_a_course_from_another_department_is_refused(poly, setup):
    """The catalogue stopped listing these; the endpoint still took them."""
    with schema_context(poly.schema_name):
        other = Department.objects.create(
            faculty=setup["dept"].faculty, code="MTH", name="Mathematics"
        )
        theirs = Subject.objects.create(
            code="MTH201", title="Algebra", credit_units=3, department=other
        )
        # No department at all is general studies, open to every programme.
        general = Subject.objects.create(code="GNS101", title="Use of English", credit_units=2)

        with pytest.raises(AcademicError) as exc:
            register_subjects(
                enrolment=setup["enrolment"], term=setup["term"], subjects=[theirs, general]
            )
        assert exc.value.code == "REGISTRATION_REJECTED"
        assert [row["subject"] for row in exc.value.details["rows"]] == ["MTH201"]
        assert exc.value.details["rows"][0]["code"] == "WRONG_DEPARTMENT"

        assert (
            len(
                register_subjects(
                    enrolment=setup["enrolment"], term=setup["term"], subjects=[general]
                )
            )
            == 1
        )


def test_a_secondary_school_has_no_departments_to_mismatch(poly, setup):
    """An enrolment with no programme is a secondary one, where the rule above
    would refuse every subject the school had filed under a department."""
    with schema_context(poly.schema_name):
        other = Department.objects.create(
            faculty=setup["dept"].faculty, code="MTH", name="Mathematics"
        )
        maths = Subject.objects.create(code="MTH101", title="Maths", department=other)
        pupil = Student.objects.create(
            admission_number="JSS/25/0001",
            first_name="Ngozi",
            last_name="Okafor",
            current_level=setup["level"],
        )
        enrolment = enrol_student(
            student=pupil, session=setup["session"], level=setup["level"], programme=None
        )
        assert (
            len(register_subjects(enrolment=enrolment, term=setup["term"], subjects=[maths])) == 1
        )


def test_a_rejected_batch_registers_nothing(poly, setup):
    from apps.academics.models import SubjectRegistration

    with schema_context(poly.schema_name):
        with pytest.raises(CreditLimitExceeded):
            register_subjects(
                enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"]
            )
        assert SubjectRegistration.objects.count() == 0


def test_below_the_minimum_is_flagged_at_approval_not_at_entry(poly, setup):
    with schema_context(poly.schema_name):
        # Two 6-unit courses = 12, under the 15-unit floor. Saving is allowed…
        register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"][:2]
        )
        # …but the semester cannot be approved like that.
        with pytest.raises(CreditLimitExceeded) as exc:
            assert_minimum_credits(setup["enrolment"], setup["term"])
        assert exc.value.code == "CREDIT_MINIMUM_NOT_MET"


def test_prerequisites_block_registration(poly, setup):
    with schema_context(poly.schema_name):
        advanced = Subject.objects.create(code="CSC201", title="Data Structures", credit_units=3)
        advanced.prerequisites.add(setup["subjects"][0])

        with pytest.raises(AcademicError) as exc:
            register_subjects(enrolment=setup["enrolment"], term=setup["term"], subjects=[advanced])
        rows = exc.value.details["rows"]
        assert rows[0]["code"] == "PREREQUISITE_NOT_MET"
        assert "CSC101" in rows[0]["message"]


def test_prerequisite_satisfied_by_passing_it_in_an_earlier_term(poly, setup):
    from apps.assessment.models import SubjectResult

    with schema_context(poly.schema_name):
        advanced = Subject.objects.create(code="CSC201", title="Data Structures", credit_units=3)
        advanced.prerequisites.add(setup["subjects"][0])

        first = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
        )[0]
        # A result alone does not satisfy a prerequisite: the registration it
        # belongs to must have cleared approval, which is the real order of
        # events — register, approve, then sit the exam.
        SubjectRegistration.objects.filter(pk=first.pk).update(status=RegistrationStatus.APPROVED)
        first.refresh_from_db()
        SubjectResult.objects.create(registration=first, percentage=65, grade="B", is_pass=True)

        second = Term.objects.create(
            session=setup["session"],
            index=2,
            name="Second Semester",
            start_date="2026-02-01",
            end_date="2026-07-31",
        )
        registrations = register_subjects(
            enrolment=setup["enrolment"], term=second, subjects=[advanced]
        )
        assert len(registrations) == 1


def test_registering_a_prerequisite_without_passing_it_is_not_enough(poly, setup):
    """Sitting CSC101 and failing it does not qualify anyone for CSC201."""
    from apps.assessment.models import SubjectResult

    with schema_context(poly.schema_name):
        advanced = Subject.objects.create(code="CSC201", title="Data Structures", credit_units=3)
        advanced.prerequisites.add(setup["subjects"][0])

        first = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
        )[0]
        SubjectResult.objects.create(registration=first, percentage=31, grade="F", is_pass=False)

        second = Term.objects.create(
            session=setup["session"],
            index=2,
            name="Second Semester",
            start_date="2026-02-01",
            end_date="2026-07-31",
        )
        with pytest.raises(AcademicError) as exc:
            register_subjects(enrolment=setup["enrolment"], term=second, subjects=[advanced])
        assert exc.value.details["rows"][0]["code"] == "PREREQUISITE_NOT_MET"


def test_registering_the_same_course_twice_in_a_term_is_refused(poly, setup):
    with schema_context(poly.schema_name):
        register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
        )
        with pytest.raises(AcademicError) as exc:
            register_subjects(
                enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
            )
        assert exc.value.details["rows"][0]["code"] == "ALREADY_REGISTERED"


def test_a_repeat_is_marked_as_a_carryover(poly, setup):
    with schema_context(poly.schema_name):
        register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
        )
        second = Term.objects.create(
            session=setup["session"],
            index=2,
            name="Second Semester",
            start_date="2026-02-01",
            end_date="2026-07-31",
        )
        again = register_subjects(
            enrolment=setup["enrolment"], term=second, subjects=[setup["subjects"][0]]
        )
        assert again[0].is_carryover is True


def test_a_course_offered_in_the_other_semester_is_refused(poly, setup):
    with schema_context(poly.schema_name):
        second_semester_only = Subject.objects.create(
            code="CSC150", title="Second semester course", credit_units=3, semester_offered=2
        )
        with pytest.raises(AcademicError) as exc:
            register_subjects(
                enrolment=setup["enrolment"],
                term=setup["term"],
                subjects=[second_semester_only],
            )
        assert exc.value.details["rows"][0]["code"] == "WRONG_SEMESTER"


def test_registration_outside_the_add_drop_window_is_refused(poly, setup):
    with schema_context(poly.schema_name):
        term = setup["term"]
        term.registration_closes_at = timezone.now() - timedelta(days=1)
        term.save()

        with pytest.raises(RegistrationClosed):
            register_subjects(
                enrolment=setup["enrolment"], term=term, subjects=[setup["subjects"][0]]
            )


def test_dropping_removes_a_course_from_the_credit_total(poly, setup):
    with schema_context(poly.schema_name):
        registrations = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"][:3]
        )
        assert registered_credit_units(setup["enrolment"], setup["term"]) == 18

        drop_subject(registration=registrations[0])
        assert registered_credit_units(setup["enrolment"], setup["term"]) == 12


def test_adviser_then_hod_approval(poly, setup):
    from apps.academics.services import approve_registration

    with schema_context(poly.schema_name):
        adviser = Staff.objects.create(
            staff_number="STF/25/0001", first_name="Ada", last_name="Nwosu"
        )
        hod = Staff.objects.create(staff_number="STF/25/0002", first_name="Bola", last_name="Ade")
        # A full load: approval also checks the credit minimum, so a single
        # 6-unit course would be refused here for the right reason.
        registration = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=setup["subjects"][:3]
        )[0]
        assert registration.status == RegistrationStatus.SUBMITTED

        approve_registration(registration=registration, staff=adviser)
        assert registration.status == RegistrationStatus.ADVISER_APPROVED

        approve_registration(registration=registration, staff=hod, as_hod=True)
        assert registration.status == RegistrationStatus.APPROVED
        assert registration.hod_approved_at is not None


def test_the_credit_minimum_is_checked_at_approval_not_at_entry(poly, setup):
    """A student may save a partial semester; an adviser may not approve one."""
    from apps.academics.services import CreditLimitExceeded, approve_registration

    with schema_context(poly.schema_name):
        adviser = Staff.objects.create(
            staff_number="STF/25/0003", first_name="Ada", last_name="Nwosu"
        )
        registration = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[setup["subjects"][0]]
        )[0]
        assert registration.status == RegistrationStatus.SUBMITTED, "6 units saves fine"

        with pytest.raises(CreditLimitExceeded) as exc:
            approve_registration(registration=registration, staff=adviser)
        assert exc.value.code == "CREDIT_MINIMUM_NOT_MET"

        # The final-year student with twelve units left to graduate.
        approve_registration(registration=registration, staff=adviser, ignore_minimum=True)
        assert registration.status == RegistrationStatus.ADVISER_APPROVED


def test_secondary_registration_skips_the_approval_workflow(make_tenant, ncc_table):
    """A JSS1 pupil offering Mathematics needs no course adviser."""
    school = make_tenant("kings-college")
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
        )
        level = ClassLevel.objects.get(code="JSS1")
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ngozi", last_name="Ali", current_level=level
        )
        enrolment = enrol_student(student=student, session=session, level=level)
        maths = Subject.objects.create(code="MTH", title="Mathematics")

        registration = register_subjects(enrolment=enrolment, term=term, subjects=[maths])[0]
        assert registration.status == RegistrationStatus.APPROVED


def test_a_prerequisite_loop_is_refused(poly):
    """CSC101 requiring CSC301 would make three courses unregisterable.

    Each of them reads as ordinary from its own side, which is why nothing
    downstream catches it: registration refuses the course and names a
    prerequisite the student cannot register either.
    """
    with schema_context(poly.schema_name):
        csc101 = Subject.objects.create(code="CSC101", title="Intro", credit_units=3)
        csc201 = Subject.objects.create(code="CSC201", title="Data Structures", credit_units=3)
        csc301 = Subject.objects.create(code="CSC301", title="Algorithms", credit_units=3)
        csc201.prerequisites.add(csc101)
        csc301.prerequisites.add(csc201)

        # Down the chain is the ordinary case and stays allowed.
        assert_no_prerequisite_cycle(csc301, [csc101])

        with pytest.raises(PrerequisiteCycle):
            assert_no_prerequisite_cycle(csc101, [csc101])

        # Two hops away, which is the one a human keying the catalogue misses.
        with pytest.raises(PrerequisiteCycle):
            assert_no_prerequisite_cycle(csc101, [csc301])

        # And the API refuses it rather than only the service.
        serializer = SubjectSerializer(
            instance=csc101, data={"prerequisites": [str(csc301.pk)]}, partial=True
        )
        with pytest.raises(PrerequisiteCycle):
            serializer.is_valid(raise_exception=True)
