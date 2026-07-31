"""Bulk score entry: the offline teacher, again.

Same shape as attendance marking, because it is the same teacher on the same
connection: one request for the whole class, one transaction, per-row errors,
and a version check so a late sync cannot overwrite an office correction.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Subject, Term
from apps.academics.services import enrol_student, register_subjects
from apps.assessment.models import AssessmentComponent, Score
from apps.assessment.services import (
    ResultsAlreadyPublished,
    ScoreEntryClosed,
    ScoreRow,
    enter_scores,
)
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
            enrolment = enrol_student(student=student, session=session, level=level, class_arm=arm)
            register_subjects(enrolment=enrolment, term=term, subjects=[maths])
            students.append(student)

        yield {
            "session": session,
            "term": term,
            "level": level,
            "arm": arm,
            "maths": maths,
            "students": students,
            "ca1": AssessmentComponent.objects.get(code="CA1"),
        }


def test_a_whole_class_is_entered_in_one_call(school, setup):
    with schema_context(school.schema_name):
        outcome = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[
                ScoreRow(student_id=str(student.pk), score=Decimal("15"))
                for student in setup["students"]
            ],
        )
        assert outcome.ok
        assert outcome.created == 3
        assert Score.objects.count() == 3


def test_a_mark_above_the_component_maximum_rolls_the_batch_back(school, setup):
    with schema_context(school.schema_name):
        outcome = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],  # marked out of 20
            rows=[
                ScoreRow(student_id=str(setup["students"][0].pk), score=Decimal("15")),
                ScoreRow(student_id=str(setup["students"][1].pk), score=Decimal("25")),
            ],
        )
        assert not outcome.ok
        assert outcome.errors[0].code == "SCORE_OUT_OF_RANGE"
        # The good row went back too: a half-entered mark sheet is worse than none.
        assert Score.objects.count() == 0


def test_an_unregistered_student_is_named_not_silently_skipped(school, setup):
    with schema_context(school.schema_name):
        stranger = Student.objects.create(
            admission_number="KC/25/0009", first_name="Musa", last_name="Bello"
        )
        outcome = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(stranger.pk), score=Decimal("10"))],
        )
        assert outcome.errors[0].code == "NOT_REGISTERED"
        assert str(stranger.pk) == outcome.errors[0].identifier


def test_a_stale_version_is_a_conflict_not_an_overwrite(school, setup):
    """The office corrected a mark while the teacher's phone was offline."""
    with schema_context(school.schema_name):
        student = setup["students"][0]
        enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=Decimal("12"))],
        )
        # The office edits: version moves to 2.
        enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=Decimal("14"), version=1)],
        )

        late_sync = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=Decimal("9"), version=1)],
        )
        assert late_sync.errors[0].code == "CONFLICT"
        assert Score.objects.get(component=setup["ca1"]).score == Decimal("14.00")


def test_a_null_score_unmarks_and_still_checks_the_version(school, setup):
    """A mark keyed against the wrong pupil has to be removable, not just editable."""
    with schema_context(school.schema_name):
        student = setup["students"][0]
        enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=Decimal("12"))],
        )

        # A stale unmark is refused like a stale edit: the stored mark is one
        # the office may have corrected since.
        stale = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=None, version=99)],
        )
        assert stale.errors[0].code == "CONFLICT"
        assert Score.objects.count() == 1

        outcome = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=None, version=1)],
        )
        assert outcome.ok
        assert outcome.deleted == 1
        assert Score.objects.count() == 0

        # Unmarking what was never marked is a no-op, not an error — the same
        # cleared field syncing twice out of an outbox.
        again = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(student.pk), score=None)],
        )
        assert again.ok
        assert again.deleted == 0


def test_a_row_outside_the_callers_scope_is_refused(school, setup):
    with schema_context(school.schema_name):
        mine = setup["students"][0]
        theirs = setup["students"][1]
        outcome = enter_scores(
            term=setup["term"],
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[
                ScoreRow(student_id=str(mine.pk), score=Decimal("15")),
                ScoreRow(student_id=str(theirs.pk), score=Decimal("15")),
            ],
            allowed_student_ids={str(mine.pk)},
        )
        assert [e.code for e in outcome.errors] == ["NOT_IN_CLASS"]


def test_entry_outside_the_result_window_is_refused(school, setup):
    with schema_context(school.schema_name):
        term = setup["term"]
        term.result_entry_closes_at = timezone.now() - timedelta(days=1)
        term.save()

        with pytest.raises(ScoreEntryClosed):
            enter_scores(
                term=term,
                subject=setup["maths"],
                component=setup["ca1"],
                rows=[ScoreRow(student_id=str(setup["students"][0].pk), score=Decimal("15"))],
            )


def test_published_results_cannot_be_edited_by_a_teacher(school, setup):
    with schema_context(school.schema_name):
        term = setup["term"]
        term.results_published_at = timezone.now()
        term.save()

        with pytest.raises(ResultsAlreadyPublished):
            enter_scores(
                term=term,
                subject=setup["maths"],
                component=setup["ca1"],
                rows=[ScoreRow(student_id=str(setup["students"][0].pk), score=Decimal("15"))],
            )

        # …but an exams officer correcting a published result may.
        outcome = enter_scores(
            term=term,
            subject=setup["maths"],
            component=setup["ca1"],
            rows=[ScoreRow(student_id=str(setup["students"][0].pk), score=Decimal("15"))],
            override_window=True,
        )
        assert outcome.ok
