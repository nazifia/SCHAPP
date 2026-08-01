"""Computing a term: totals, grades, positions, GPA, CGPA, publication."""

from decimal import Decimal

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Department,
    Faculty,
    Programme,
    Subject,
    Term,
)
from apps.academics.services import enrol_student, register_subjects
from apps.assessment.models import AssessmentComponent, SubjectResult, TermResult
from apps.assessment.services import (
    ResultsIncomplete,
    ScoreRow,
    enter_scores,
    publish_results,
    recompute_term,
)
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


def mark(term, subject, component, pairs):
    """`pairs` is [(student, score), …] for one component."""
    return enter_scores(
        term=term,
        subject=subject,
        component=component,
        rows=[ScoreRow(student_id=str(s.pk), score=Decimal(v)) for s, v in pairs],
    )


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def secondary(school):
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

        students = []
        for index, (first, last) in enumerate(
            [("Ngozi", "Ali"), ("Chidi", "Eze"), ("Bola", "Ade")], start=1
        ):
            student = Student.objects.create(
                admission_number=f"KC/25/000{index}",
                first_name=first,
                last_name=last,
                current_level=level,
                current_arm=arm,
            )
            register_subjects(
                enrolment=enrol_student(
                    student=student, session=session, level=level, class_arm=arm
                ),
                term=term,
                subjects=[maths],
            )
            students.append(student)

        components = {c.code: c for c in AssessmentComponent.objects.all()}
        yield {
            "session": session,
            "term": term,
            "level": level,
            "arm": arm,
            "maths": maths,
            "students": students,
            "components": components,
        }


def enter_full_marks(setup, marks: dict) -> None:
    """`marks` is {student: (ca1, ca2, exam)}."""
    for code, offset in [("CA1", 0), ("CA2", 1), ("EXAM", 2)]:
        mark(
            setup["term"],
            setup["maths"],
            setup["components"][code],
            [(student, values[offset]) for student, values in marks.items()],
        )


def test_totals_percentage_and_grade(school, secondary):
    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        # The third student must land *below* the 40% pass mark, not on it:
        # 10+10+20 is exactly 40, which is an E8 and a pass in the WAEC scale.
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (8, 8, 18)},
        )
        recompute_term(secondary["term"])

        best = SubjectResult.objects.get(registration__enrolment__student=first)
        assert best.total_score == Decimal("90.00")
        assert best.max_total == Decimal("100.00")
        assert best.percentage == Decimal("90.00")
        assert best.grade == "A1"
        assert best.is_pass is True
        assert best.is_complete is True

        worst = SubjectResult.objects.get(registration__enrolment__student=third)
        assert worst.grade == "F9"
        assert worst.is_pass is False


def test_positions_and_class_statistics(school, secondary):
    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])

        results = {
            str(r.registration.enrolment.student_id): r
            for r in SubjectResult.objects.select_related("registration__enrolment")
        }
        assert results[str(first.pk)].position == 1
        assert results[str(third.pk)].position == 3
        assert results[str(first.pk)].cohort_size == 3
        assert results[str(first.pk)].class_highest == Decimal("90.00")
        assert results[str(first.pk)].class_lowest == Decimal("40.00")
        # (90 + 60 + 40) / 3
        assert results[str(first.pk)].class_average == Decimal("63.33")


def test_a_tie_shares_a_position(school, secondary):
    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (18, 18, 54), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])

        positions = sorted(SubjectResult.objects.values_list("position", flat=True))
        assert positions == [1, 1, 3]


def test_a_missing_component_leaves_the_result_incomplete(school, secondary):
    with schema_context(school.schema_name):
        first = secondary["students"][0]
        # Only the CA is in: the exam has not been marked yet.
        mark(secondary["term"], secondary["maths"], secondary["components"]["CA1"], [(first, 18)])
        recompute_term(secondary["term"])

        result = SubjectResult.objects.get(registration__enrolment__student=first)
        assert result.is_complete is False

        with pytest.raises(ResultsIncomplete) as exc:
            publish_results(secondary["term"])
        assert exc.value.details["incomplete"] >= 1

        # Forcing it is allowed, because a subject with no exam is a real case.
        publish_results(secondary["term"], force=True)
        secondary["term"].refresh_from_db()
        assert secondary["term"].results_published_at is not None


def test_publishing_a_complete_term(school, secondary):
    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])
        publish_results(secondary["term"])

        secondary["term"].refresh_from_db()
        assert secondary["term"].results_published


def test_recomputing_twice_changes_nothing(school, secondary):
    """A correction two weeks later must be safe to apply."""
    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])
        before = list(SubjectResult.objects.order_by("pk").values_list("percentage", "position"))

        recompute_term(secondary["term"])
        after = list(SubjectResult.objects.order_by("pk").values_list("percentage", "position"))
        assert before == after
        assert SubjectResult.objects.count() == 3
        assert TermResult.objects.count() == 3


def test_a_secondary_term_result_has_no_gpa(school, secondary):
    with schema_context(school.schema_name):
        first = secondary["students"][0]
        enter_full_marks(secondary, {first: (18, 18, 54)})
        recompute_term(secondary["term"])

        result = TermResult.objects.get(enrolment__student=first)
        assert result.gpa is None
        assert result.cgpa is None
        assert result.average == Decimal("90.00")
        assert result.position == 1


def test_tertiary_gpa_and_cgpa_across_two_semesters(make_tenant, ncc_table):
    poly = make_tenant("unity-poly", institution_type="TERTIARY")
    with schema_context(poly.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        first_semester = Term.objects.create(
            session=session,
            index=1,
            name="First Semester",
            start_date="2025-09-01",
            end_date="2026-01-31",
            is_current=True,
        )
        second_semester = Term.objects.create(
            session=session,
            index=2,
            name="Second Semester",
            start_date="2026-02-01",
            end_date="2026-07-31",
        )
        faculty = Faculty.objects.create(code="SCI", name="Science")
        department = Department.objects.create(faculty=faculty, code="CSC", name="Computing")
        programme = Programme.objects.create(
            department=department, code="ND-CSC", name="Computer Science"
        )
        level = ClassLevel.objects.get(code="100")
        course = Subject.objects.create(code="CSC101", title="Computing", credit_units=4)

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
        components = {c.code: c for c in AssessmentComponent.objects.all()}

        # An A in the first semester (75%), a C in the second (55%).
        for term, (ca, exam) in [
            (first_semester, (25, 50)),
            (second_semester, (18, 37)),
        ]:
            register_subjects(enrolment=enrolment, term=term, subjects=[course])
            registration = enrolment.subject_registrations.get(term=term)
            registration.status = "APPROVED"
            registration.save()
            mark(term, course, components["CA"], [(student, ca)])
            mark(term, course, components["EXAM"], [(student, exam)])
            recompute_term(term)

        first = TermResult.objects.get(enrolment=enrolment, term=first_semester)
        second = TermResult.objects.get(enrolment=enrolment, term=second_semester)

        assert first.gpa == Decimal("5.00")
        assert first.cgpa == Decimal("5.00")
        assert second.gpa == Decimal("3.00")
        # Equal units both semesters, so the CGPA is the plain mean of the two.
        assert second.cgpa == Decimal("4.00")
        assert second.credit_units_earned == 4


def test_a_tertiary_cohort_is_ranked_on_the_gpa_not_the_average(make_tenant, ncc_table):
    """Units are the whole point: a good mark in a one-unit course is worth less
    than a fair one in a four-unit course, and the position must say so."""
    poly = make_tenant("credit-poly", institution_type="TERTIARY")
    with schema_context(poly.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        semester = Term.objects.create(
            session=session,
            index=1,
            name="First Semester",
            start_date="2025-09-01",
            end_date="2026-01-31",
            is_current=True,
        )
        faculty = Faculty.objects.create(code="SCI", name="Science")
        department = Department.objects.create(faculty=faculty, code="CSC", name="Computing")
        programme = Programme.objects.create(
            department=department, code="ND-CSC", name="Computer Science"
        )
        level = ClassLevel.objects.get(code="100")
        heavy = Subject.objects.create(code="CSC101", title="Computing", credit_units=4)
        light = Subject.objects.create(code="GNS101", title="Use of English", credit_units=1)
        components = {c.code: c for c in AssessmentComponent.objects.all()}

        students = []
        for index, (first, last) in enumerate([("Chidi", "Eze"), ("Ngozi", "Ali")], start=1):
            student = Student.objects.create(
                admission_number=f"CSC/25/000{index}",
                first_name=first,
                last_name=last,
                programme=programme,
                current_level=level,
            )
            enrolment = enrol_student(
                student=student, session=session, level=level, programme=programme
            )
            register_subjects(enrolment=enrolment, term=semester, subjects=[heavy, light])
            enrolment.subject_registrations.update(status="APPROVED")
            students.append(student)

        chidi, ngozi = students
        # Chidi: 71% in the four-unit course, 41% in the one-unit — average 56.
        # Ngozi: 45% and 95% — average 70, the better mean and the worse degree.
        for subject, marks in [
            (heavy, {chidi: (21, 50), ngozi: (15, 30)}),
            (light, {chidi: (11, 30), ngozi: (28, 67)}),
        ]:
            mark(semester, subject, components["CA"], [(s, ca) for s, (ca, _) in marks.items()])
            mark(semester, subject, components["EXAM"], [(s, x) for s, (_, x) in marks.items()])
        recompute_term(semester)

        first = TermResult.objects.get(enrolment__student=chidi)
        second = TermResult.objects.get(enrolment__student=ngozi)
        assert (first.average, first.gpa) == (Decimal("56.00"), Decimal("4.20"))
        assert (second.average, second.gpa) == (Decimal("70.00"), Decimal("2.60"))
        assert first.position == 1
        assert second.position == 2

        # A tertiary result names the department and the course of study it was
        # earned in — on the sheet, and on the student's own record.
        from apps.assessment.selectors import broadsheet, transcript_context

        sheet = broadsheet(term=semester, level=level)
        assert sheet["department"] == "Computing"
        assert sheet["programme"] == "ND Computer Science"
        assert sheet["mixed_programmes"] is False
        assert {row["programme"] for row in sheet["rows"]} == {"ND Computer Science"}

        transcript = transcript_context(chidi)
        assert transcript["department"] == "Computing"
        assert transcript["programme"] == "ND Computer Science"
        assert transcript["terms"][0]["department"] == "Computing"


def test_seeing_every_student_is_not_seeing_every_mark(school, secondary):
    """A bursar may look up any pupil in the school. Marks are a separate grant.

    `self.view_own_results` was in the catalogue with nothing checking it, so
    result visibility rode entirely on student visibility — which handed the
    bursar, the librarian and the hostel warden the whole school's marks.
    """
    from apps.accounts.models import User
    from apps.accounts.services import assign_role, seed_roles
    from apps.assessment.selectors import results_visible_to

    with schema_context(school.schema_name):
        seed_roles()
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])
        publish_results(secondary["term"])

        bursar = User.objects.create_user("+2348031111111", first_name="Bursar")
        assign_role(bursar, "bursar")
        registrar = User.objects.create_user("+2348032222222", first_name="Registrar")
        assign_role(registrar, "registrar")

        # Both see all three students; only one of them may read the results.
        from apps.people.selectors import students_visible_to

        assert students_visible_to(bursar).count() == 3
        assert results_visible_to(bursar).count() == 0
        assert results_visible_to(registrar).count() == 3


def test_a_student_reads_their_own_published_result(school, secondary):
    from apps.accounts.models import User
    from apps.accounts.services import assign_role, seed_roles
    from apps.assessment.selectors import results_visible_to

    with schema_context(school.schema_name):
        seed_roles()
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])

        pupil_user = User.objects.create_user("+2348033333333", first_name="Ngozi")
        assign_role(pupil_user, "student")  # carries self.view_own_results
        first.user = pupil_user
        first.save()

        # Computed but not published: nothing yet.
        assert results_visible_to(pupil_user).count() == 0

        publish_results(secondary["term"])
        visible = results_visible_to(pupil_user)
        assert [r.enrolment.student for r in visible] == [first]


# ---------------------------------------------------------------------------
# A rebuild removes as well as writes
# ---------------------------------------------------------------------------
def test_a_dropped_course_stops_appearing_on_the_result(school, secondary):
    """`recompute_term` wrote every row it computed and removed none.

    A registration that leaves `COUNTED_STATUSES` — dropped or refused — kept
    the `SubjectResult` it had already earned, and `broadsheet`,
    `report_card_context` and `transcript_context` all filter on the term
    alone. So a course the pupil no longer takes went on printing on their
    report card, against `transcript_context`'s own promise that "withdrawn or
    dropped courses never appear".
    """
    from apps.academics.models import SubjectRegistration
    from apps.academics.services import drop_subject
    from apps.assessment.selectors import transcript_context

    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        enter_full_marks(
            secondary,
            {first: (18, 18, 54), second: (12, 14, 34), third: (10, 10, 20)},
        )
        recompute_term(secondary["term"])
        assert SubjectResult.objects.count() == 3
        assert TermResult.objects.count() == 3

        registration = SubjectRegistration.objects.get(enrolment__student=third)
        drop_subject(registration=registration, ignore_window=True)
        recompute_term(secondary["term"])

        # Maths was their only subject, so the whole result goes with it.
        assert not SubjectResult.objects.filter(registration=registration).exists()
        assert not TermResult.objects.filter(enrolment__student=third).exists()
        # The two who still take it are untouched, and re-ranked among themselves.
        assert SubjectResult.objects.count() == 2
        assert {r.cohort_size for r in SubjectResult.objects.all()} == {2}
        assert transcript_context(third)["terms"] == []


def test_a_dropped_course_no_longer_blocks_publication(school, secondary):
    """The dead end this caused: an unfinished result for a course nobody takes.

    `publish_results` counts `is_complete=False` over every `SubjectResult` in
    the term, and `enter_scores` refuses a dropped registration with
    `NOT_REGISTERED` — so the marks could never be completed and `force` was
    the only way a term ever got published again.
    """
    from apps.academics.models import SubjectRegistration
    from apps.academics.services import drop_subject

    with schema_context(school.schema_name):
        first, second, third = secondary["students"]
        # Everyone has CA1 only: three results, all incomplete.
        mark(
            secondary["term"],
            secondary["maths"],
            secondary["components"]["CA1"],
            [(student, 15) for student in secondary["students"]],
        )
        recompute_term(secondary["term"])
        with pytest.raises(ResultsIncomplete):
            publish_results(secondary["term"])

        # The one pupil who dropped it is no longer a reason to hold the term.
        drop_subject(
            registration=SubjectRegistration.objects.get(enrolment__student=third),
            ignore_window=True,
        )
        recompute_term(secondary["term"])
        assert SubjectResult.objects.filter(is_complete=False).count() == 2

        enter_full_marks(secondary, {first: (18, 18, 54), second: (12, 14, 34)})
        recompute_term(secondary["term"])
        publish_results(secondary["term"])  # no `force`
        secondary["term"].refresh_from_db()
        assert secondary["term"].results_published_at is not None
