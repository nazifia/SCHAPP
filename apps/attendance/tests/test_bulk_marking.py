"""Bulk attendance — the offline teacher's sync path."""

from datetime import date

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Term
from apps.attendance.models import AttendanceStatus, StudentAttendance
from apps.attendance.services import AttendanceRow, mark_bulk
from apps.people.models import Staff, Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

TODAY = date(2025, 10, 6)


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def register(school):
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
        arm = ClassArm.objects.create(level=level, name="A", capacity=50)
        students = [
            Student.objects.create(
                admission_number=f"KC/25/{n:04d}",
                first_name=f"Pupil{n}",
                last_name="Test",
                current_level=level,
                current_arm=arm,
            )
            for n in range(1, 41)
        ]
        teacher = Staff.objects.create(
            staff_number="STF/25/0001", first_name="Amaka", last_name="Obi"
        )
        yield {"term": term, "arm": arm, "students": students, "teacher": teacher}


def _rows(students, status=AttendanceStatus.PRESENT, **over):
    return [AttendanceRow(student_id=str(s.pk), status=status, **over) for s in students]


def test_a_class_of_forty_is_one_request(school, register):
    with schema_context(school.schema_name):
        outcome = mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows(register["students"]),
            class_arm=register["arm"],
            marked_by=register["teacher"],
        )
        assert outcome.ok
        assert outcome.created == 40
        assert StudentAttendance.objects.count() == 40


def test_resyncing_the_same_register_updates_rather_than_duplicates(school, register):
    with schema_context(school.schema_name):
        mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows(register["students"][:5]),
            class_arm=register["arm"],
        )
        outcome = mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows(register["students"][:5], status=AttendanceStatus.ABSENT),
            class_arm=register["arm"],
        )
        assert outcome.updated == 5
        assert StudentAttendance.objects.count() == 5
        assert StudentAttendance.objects.filter(status=AttendanceStatus.ABSENT).count() == 5


def test_one_bad_row_rolls_back_the_whole_batch(school, register):
    with schema_context(school.schema_name):
        rows = _rows(register["students"][:3])
        rows.append(AttendanceRow(student_id=str(register["students"][3].pk), status="NAPPING"))

        outcome = mark_bulk(term=register["term"], date=TODAY, rows=rows, class_arm=register["arm"])

        assert not outcome.ok
        assert outcome.errors[0].code == "BAD_STATUS"
        assert outcome.created == 0
        assert StudentAttendance.objects.count() == 0


def test_errors_name_the_offending_row(school, register):
    with schema_context(school.schema_name):
        stranger = Student.objects.create(
            admission_number="KC/25/9999", first_name="Not", last_name="Here"
        )
        rows = _rows(register["students"][:2]) + _rows([stranger])
        allowed = {str(s.pk) for s in register["students"]}

        outcome = mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=rows,
            class_arm=register["arm"],
            allowed_student_ids=allowed,
        )

        assert [e.code for e in outcome.errors] == ["NOT_IN_CLASS"]
        assert outcome.errors[0].index == 2
        assert outcome.errors[0].identifier == str(stranger.pk)


def test_a_stale_device_cannot_silently_overwrite_a_correction(school, register):
    """Conflict resolution: last-write-wins, but only against the seen version."""
    with schema_context(school.schema_name):
        student = register["students"][0]
        mark_bulk(
            term=register["term"], date=TODAY, rows=_rows([student]), class_arm=register["arm"]
        )
        record = StudentAttendance.objects.get()
        assert record.version == 1

        # Office corrects it to EXCUSED; version becomes 2.
        mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=[
                AttendanceRow(
                    student_id=str(student.pk), status=AttendanceStatus.EXCUSED, version=1
                )
            ],
            class_arm=register["arm"],
        )
        record.refresh_from_db()
        assert record.version == 2

        # The teacher's phone, offline since this morning, still thinks it is v1.
        outcome = mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=[
                AttendanceRow(student_id=str(student.pk), status=AttendanceStatus.ABSENT, version=1)
            ],
            class_arm=register["arm"],
        )

        assert not outcome.ok
        assert outcome.errors[0].code == "CONFLICT"
        record.refresh_from_db()
        assert record.status == AttendanceStatus.EXCUSED  # correction survived


def test_a_row_with_no_version_creates_without_conflict(school, register):
    with schema_context(school.schema_name):
        outcome = mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows(register["students"][:1]),
            class_arm=register["arm"],
        )
        assert outcome.created == 1


def test_daily_register_and_subject_register_coexist(school, register):
    from apps.academics.models import Subject

    with schema_context(school.schema_name):
        maths = Subject.objects.create(code="MTH", title="Mathematics")
        student = register["students"][0]

        mark_bulk(
            term=register["term"], date=TODAY, rows=_rows([student]), class_arm=register["arm"]
        )
        mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows([student]),
            subject=maths,
            class_arm=register["arm"],
        )

        assert StudentAttendance.objects.count() == 2


def test_the_daily_register_can_be_asked_for_on_its_own(school, register):
    """`subject__isnull=true` is what the register screen filters on.

    Without it the morning register reads a chemistry period's row for the same
    pupil and day — and sends that row's `version` back, which comes home as a
    conflict on a record the form teacher never opened.
    """
    from django_filters.rest_framework import DjangoFilterBackend

    from apps.academics.models import Subject
    from apps.attendance.views import StudentAttendanceViewSet

    with schema_context(school.schema_name):
        maths = Subject.objects.create(code="MTH", title="Mathematics")
        student = register["students"][0]
        mark_bulk(
            term=register["term"], date=TODAY, rows=_rows([student]), class_arm=register["arm"]
        )
        mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows([student]),
            subject=maths,
            class_arm=register["arm"],
        )

        queryset = StudentAttendance.objects.all()
        filterset_class = DjangoFilterBackend().get_filterset_class(
            StudentAttendanceViewSet(), queryset
        )
        filterset = filterset_class(data={"subject__isnull": "true"}, queryset=queryset)

        assert filterset.is_valid(), filterset.errors
        assert [record.subject_id for record in filterset.qs] == [None]


def test_the_device_timestamp_is_kept_not_the_sync_time(school, register):
    from django.utils import timezone

    with schema_context(school.schema_name):
        marked_at = timezone.now() - timezone.timedelta(hours=6)
        mark_bulk(
            term=register["term"],
            date=TODAY,
            rows=_rows(register["students"][:1], recorded_at=marked_at),
            class_arm=register["arm"],
        )
        record = StudentAttendance.objects.get()
        assert abs((record.recorded_at - marked_at).total_seconds()) < 1
