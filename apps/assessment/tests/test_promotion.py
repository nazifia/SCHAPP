"""End-of-session promotion: decide, override, apply.

The engine promised in Phase 3, now that there are results to decide on.
"""

from decimal import Decimal

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Department,
    Enrolment,
    Faculty,
    Programme,
    Subject,
    Term,
)
from apps.academics.services import enrol_student, register_subjects
from apps.assessment.models import AssessmentComponent, PromotionDecision, TermResult
from apps.assessment.services import (
    ScoreRow,
    apply_promotions,
    decide_promotions,
    enter_scores,
    override_promotion,
    recompute_term,
)
from apps.people.models import Student, StudentStatus
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def build_cohort(*, level_code: str, marks: dict) -> dict:
    """A session with one term and one subject.

    `marks` is {name: (ca1, ca2, exam)} out of 20/20/60.
    """
    session = AcademicSession.objects.create(
        name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
    )
    next_session = AcademicSession.objects.create(
        name="2026/2027", start_date="2026-09-01", end_date="2027-07-31"
    )
    term = Term.objects.create(
        session=session,
        index=1,
        name="First Term",
        start_date="2025-09-01",
        end_date="2025-12-15",
        is_current=True,
    )
    level = ClassLevel.objects.get(code=level_code)
    arm = ClassArm.objects.create(level=level, name="A")
    maths = Subject.objects.create(code="MTH", title="Mathematics")
    components = {c.code: c for c in AssessmentComponent.objects.all()}

    students = {}
    for index, (name, values) in enumerate(marks.items(), start=1):
        student = Student.objects.create(
            admission_number=f"KC/25/000{index}",
            first_name=name,
            last_name="Test",
            current_level=level,
            current_arm=arm,
        )
        register_subjects(
            enrolment=enrol_student(student=student, session=session, level=level, class_arm=arm),
            term=term,
            subjects=[maths],
        )
        for code, value in zip(("CA1", "CA2", "EXAM"), values, strict=True):
            enter_scores(
                term=term,
                subject=maths,
                component=components[code],
                rows=[ScoreRow(student_id=str(student.pk), score=Decimal(value))],
            )
        students[name] = student

    recompute_term(term)
    return {
        "session": session,
        "next_session": next_session,
        "term": term,
        "level": level,
        "students": students,
    }


def test_a_pass_promotes_and_a_fail_repeats(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="JSS1", marks={"Ngozi": (20, 20, 50), "Musa": (5, 5, 10)})
        decisions = {
            d["student"].split()[0]: d for d in decide_promotions(session=setup["session"])
        }

        assert decisions["Ngozi"]["decision"] == PromotionDecision.PROMOTE
        # 20% for the session: below the scale's 40% pass mark.
        assert decisions["Musa"]["decision"] == PromotionDecision.REPEAT
        assert "JSS2" in decisions["Ngozi"]["note"]


def test_the_terminal_level_graduates_instead_of_promoting(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="SSS3", marks={"Bola": (20, 20, 15)})
        decisions = decide_promotions(session=setup["session"])
        assert decisions[0]["decision"] == PromotionDecision.GRADUATE


def test_a_student_with_no_results_is_left_undecided(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="JSS1", marks={"Ngozi": (20, 20, 50)})
        newcomer = Student.objects.create(
            admission_number="KC/25/0099", first_name="Late", last_name="Arrival"
        )
        enrol_student(student=newcomer, session=setup["session"], level=setup["level"])

        decisions = decide_promotions(session=setup["session"])
        assert [d["student"] for d in decisions] == ["Ngozi Test"]


def test_applying_promotions_creates_next_sessions_enrolments(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="JSS1", marks={"Ngozi": (20, 20, 50), "Musa": (5, 5, 10)})
        decide_promotions(session=setup["session"])
        counts = apply_promotions(session=setup["session"], next_session=setup["next_session"])

        assert counts == {"promoted": 1, "repeated": 1, "graduated": 0, "skipped": 0}

        promoted = Enrolment.objects.get(
            student=setup["students"]["Ngozi"], session=setup["next_session"]
        )
        assert promoted.level.code == "JSS2"
        repeated = Enrolment.objects.get(
            student=setup["students"]["Musa"], session=setup["next_session"]
        )
        assert repeated.level.code == "JSS1"


def test_applying_promotions_twice_does_not_double_enrol(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="JSS1", marks={"Ngozi": (20, 20, 50)})
        decide_promotions(session=setup["session"])
        apply_promotions(session=setup["session"], next_session=setup["next_session"])
        second_run = apply_promotions(session=setup["session"], next_session=setup["next_session"])

        # Nothing is ACTIVE in the old session any more, so the second run is a
        # no-op rather than a second enrolment.
        assert second_run["promoted"] == 0
        assert (
            Enrolment.objects.filter(
                student=setup["students"]["Ngozi"], session=setup["next_session"]
            ).count()
            == 1
        )


def test_graduating_closes_the_student_record(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="SSS3", marks={"Bola": (20, 20, 15)})
        decide_promotions(session=setup["session"])
        apply_promotions(session=setup["session"], next_session=setup["next_session"])

        student = setup["students"]["Bola"]
        student.refresh_from_db()
        assert student.status == StudentStatus.GRADUATED
        assert student.date_left is not None


def test_a_tertiary_level_moves_on_the_cgpa_of_the_final_semester(make_tenant, ncc_table):
    """Two semesters, and it is the CGPA standing at the end that decides."""
    poly = make_tenant("unity-poly", institution_type="TERTIARY")
    with schema_context(poly.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        semesters = [
            Term.objects.create(
                session=session,
                index=index,
                name=name,
                start_date=start,
                end_date=end,
                is_current=index == 1,
            )
            for index, name, start, end in [
                (1, "First Semester", "2025-09-01", "2026-01-31"),
                (2, "Second Semester", "2026-02-01", "2026-07-31"),
            ]
        ]
        department = Department.objects.create(
            faculty=Faculty.objects.create(code="SCI", name="Science"), code="CSC", name="Computing"
        )
        programme = Programme.objects.create(
            department=department, code="ND-CSC", name="Computer Science"
        )
        level = ClassLevel.objects.get(code="100")
        course = Subject.objects.create(code="CSC101", title="Computing", credit_units=4)
        components = {c.code: c for c in AssessmentComponent.objects.all()}

        marks = {"Chidi": (25, 50), "Musa": (5, 10)}  # 75% (A) against 15% (F)
        for index, (name, (ca, exam)) in enumerate(marks.items(), start=1):
            student = Student.objects.create(
                admission_number=f"CSC/25/000{index}",
                first_name=name,
                last_name="Test",
                programme=programme,
                current_level=level,
            )
            enrolment = enrol_student(
                student=student, session=session, level=level, programme=programme
            )
            for term in semesters:
                register_subjects(enrolment=enrolment, term=term, subjects=[course])
                for code, value in (("CA", ca), ("EXAM", exam)):
                    enter_scores(
                        term=term,
                        subject=course,
                        component=components[code],
                        rows=[ScoreRow(student_id=str(student.pk), score=Decimal(value))],
                    )

        for term in semesters:
            recompute_term(term)

        decisions = {d["student"].split()[0]: d for d in decide_promotions(session=session)}

        assert decisions["Chidi"]["cgpa"] == Decimal("5.00")
        assert decisions["Chidi"]["decision"] == PromotionDecision.PROMOTE
        assert "200" in decisions["Chidi"]["note"]
        assert "CGPA" in decisions["Chidi"]["note"]
        # 0.00 is below the 1.50 line, even though the percentage rule is not
        # what was applied here.
        assert decisions["Musa"]["decision"] == PromotionDecision.REPEAT

        # The bar is per-institution, so a school that admits at 1.00 gets its way.
        lenient = {
            d["student"].split()[0]: d
            for d in decide_promotions(session=session, min_cgpa=Decimal("0.00"))
        }
        assert lenient["Musa"]["decision"] == PromotionDecision.PROMOTE


def test_an_override_beats_the_engine_and_is_written_down(school):
    with schema_context(school.schema_name):
        setup = build_cohort(level_code="JSS1", marks={"Musa": (5, 5, 10)})
        decide_promotions(session=setup["session"])

        term_result = TermResult.objects.get(enrolment__student=setup["students"]["Musa"])
        override_promotion(
            term_result=term_result,
            decision=PromotionDecision.PROMOTE,
            reason="Illness during the examination; medical report on file.",
        )

        term_result.refresh_from_db()
        assert term_result.promotion_status == PromotionDecision.PROMOTE
        assert "Illness" in term_result.enrolment.promotion_note

        counts = apply_promotions(session=setup["session"], next_session=setup["next_session"])
        assert counts["promoted"] == 1
