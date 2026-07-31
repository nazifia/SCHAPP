"""Reads and teaching-scope rules for the academic structure."""

from django.db.models import Count, F, Q, QuerySet

from .models import AcademicSession, ClassArm, Enrolment, Subject, Term, TimetableEntry


def current_session() -> AcademicSession | None:
    return AcademicSession.objects.filter(is_current=True).first()


def current_term() -> Term | None:
    return Term.objects.filter(is_current=True).select_related("session").first()


def arms_taught_by(staff) -> QuerySet[ClassArm]:
    """Arms a member of staff may act on: taught, or form class."""
    if staff is None:
        return ClassArm.objects.none()
    return (
        ClassArm.objects.filter(Q(assignments__staff=staff) | Q(form_teacher=staff))
        .distinct()
        .select_related("level", "stream")
    )


def subjects_taught_by(staff, *, term=None) -> QuerySet[Subject]:
    if staff is None:
        return Subject.objects.none()
    assignments = staff.teaching_assignments.all()
    if term is not None:
        assignments = assignments.filter(Q(term=term) | Q(term__isnull=True))
    return Subject.objects.filter(pk__in=assignments.values("subject_id")).distinct()


def subjects_offered_to(enrolment: Enrolment, *, term=None) -> QuerySet[Subject]:
    """The catalogue one student can actually pick from.

    `check_registration` already refuses the wrong stream and the wrong
    semester, but only after the course has been ticked and sent — the
    registration screen was listing every active subject in the school, so a
    tertiary catalogue of several hundred courses arrived whole and the
    refusals arrived one round trip later.

    A null on the subject means "not restricted that way", so each rule reads
    "mine, or nobody's": a general-studies course with no department is offered
    to every programme, and a subject with no stream to every stream.

    Level is the one bound that is not equality. A carryover is by definition a
    course from a level the student has already left — `_is_carryover` exists
    to mark exactly that — so anything at or below theirs stays listed and only
    the levels above come off.
    """
    subjects = Subject.objects.filter(is_active=True)

    if enrolment.level_id:
        subjects = subjects.filter(
            Q(level__order__lte=enrolment.level.order) | Q(level__isnull=True)
        )
    if enrolment.programme_id:
        subjects = subjects.filter(
            Q(department=enrolment.programme.department_id) | Q(department__isnull=True)
        )

    stream_id = enrolment.student.stream_id
    subjects = subjects.filter(
        (Q(stream=stream_id) | Q(stream__isnull=True)) if stream_id else Q(stream__isnull=True)
    )

    if term is not None:
        subjects = subjects.filter(
            Q(semester_offered=term.index) | Q(semester_offered__isnull=True)
        )
    return subjects


def teaches(staff, *, subject, class_arm=None, level=None, term=None) -> bool:
    """The gate for score entry and attendance marking."""
    if staff is None:
        return False
    assignments = staff.teaching_assignments.filter(subject=subject)
    if term is not None:
        assignments = assignments.filter(Q(term=term) | Q(term__isnull=True))
    if class_arm is not None:
        assignments = assignments.filter(class_arm=class_arm)
    elif level is not None:
        assignments = assignments.filter(level=level)
    return assignments.exists()


def class_list(*, session, class_arm) -> QuerySet[Enrolment]:
    """The register order: by roll number, unnumbered pupils last, then by name.

    `roll_number` was declared from the start and sorted on by nothing — a
    class list that ignores it is a register the teacher has to re-sort by
    hand against the one printed on the wall.
    """
    return (
        Enrolment.objects.filter(session=session, class_arm=class_arm, status="ACTIVE")
        .select_related("student", "level", "class_arm")
        .order_by(
            F("roll_number").asc(nulls_last=True), "student__last_name", "student__first_name"
        )
    )


def unplaced_enrolments(*, session, level=None) -> QuerySet[Enrolment]:
    """Active students with no class this session — the allocation worklist.

    Everyone `apply_promotions` moved up lands here until an arm is assigned.
    """
    queryset = Enrolment.objects.filter(
        session=session, class_arm__isnull=True, status="ACTIVE"
    ).select_related("student", "level", "programme")
    if level is not None:
        queryset = queryset.filter(level=level)
    return queryset.order_by("level__order", "student__last_name", "student__first_name")


def with_occupancy(queryset: QuerySet[ClassArm], session) -> QuerySet[ClassArm]:
    """Annotate `enrolled` so a caller can see which arms have seats left."""
    if session is None:
        return queryset
    return queryset.annotate(
        enrolled=Count(
            "enrolments",
            filter=Q(enrolments__session=session, enrolments__status="ACTIVE"),
            distinct=True,
        )
    )


def timetable_for_arm(*, term, class_arm) -> QuerySet[TimetableEntry]:
    return (
        TimetableEntry.objects.filter(term=term, class_arm=class_arm)
        .select_related("subject", "staff", "room")
        .order_by("day", "start_time")
    )


def timetable_for_staff(*, term, staff) -> QuerySet[TimetableEntry]:
    return (
        TimetableEntry.objects.filter(term=term, staff=staff)
        .select_related("subject", "room", "class_arm", "level")
        .order_by("day", "start_time")
    )
