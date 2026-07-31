"""Enrolment, course registration and timetabling rules.

All of it lives here rather than in views so the same rules apply to the API,
a CSV import and a management command.
"""

import logging
from dataclasses import dataclass

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.api.exceptions import AppError
from apps.tenants.db import tenant_atomic

from .models import (
    AcademicSession,
    ClassArm,
    Enrolment,
    EnrolmentStatus,
    RegistrationStatus,
    Subject,
    SubjectRegistration,
    Term,
    TimetableEntry,
)

logger = logging.getLogger(__name__)


class AcademicError(AppError):
    default_code = "ACADEMIC_ERROR"


class ArmFull(AcademicError):
    default_code = "ARM_FULL"
    default_detail = "That class is already at capacity."


class ArmInactive(AcademicError):
    default_code = "ARM_INACTIVE"
    default_detail = "That class is no longer in use."


class ArmMismatch(AcademicError):
    """The class does not belong to the level or stream the student is in."""

    default_code = "ARM_MISMATCH"


class EnrolmentNotActive(AcademicError):
    default_code = "ENROLMENT_NOT_ACTIVE"
    default_detail = "Only an active enrolment can be placed in a class."


class RegistrationClosed(AcademicError):
    default_code = "REGISTRATION_CLOSED"
    default_detail = "Course registration is not open for this semester."


class CreditLimitExceeded(AcademicError):
    default_code = "CREDIT_LIMIT_EXCEEDED"


class PrerequisiteNotMet(AcademicError):
    default_code = "PREREQUISITE_NOT_MET"


class PrerequisiteCycle(AcademicError):
    """The catalogue would require a course to be passed before itself."""

    default_code = "PREREQUISITE_CYCLE"


class RegistrationNotPending(AcademicError):
    """The registration is not in a state this step can act on."""

    default_code = "REGISTRATION_NOT_PENDING"


class TimetableClash(AcademicError):
    default_code = "TIMETABLE_CLASH"


class InvalidPeriod(AcademicError):
    default_code = "INVALID_PERIOD"


# ---------------------------------------------------------------------------
# The current period
# ---------------------------------------------------------------------------
def _term_for_today(session: AcademicSession) -> Term | None:
    """The term of `session` today falls in, else its first.

    Between terms — or during the long holiday — no term contains today, and
    the school still needs somewhere for fees, registration and the timetable
    to point. The earliest term is the one a rollover is heading into.
    """
    today = timezone.localdate()
    terms = session.terms.order_by("index")
    return terms.filter(start_date__lte=today, end_date__gte=today).first() or terms.first()


@tenant_atomic()
def set_current_session(*, session: AcademicSession, actor=None) -> AcademicSession:
    """Roll the school over to `session`, and move the current term with it.

    Was reachable only as `is_current` on a PATCH, which wrote the column and
    left `Term.is_current` on the year just closed — score entry, course
    registration and the timetable read the term, while enrolment, class lists
    and invoicing read the session, so the school ran in two years at once.

    The term is chosen by the calendar (`_term_for_today`), not asked for: an
    administrator flipping a year over should not have to remember the second
    flag. A session with no terms yet leaves no current term at all rather than
    guessing — `TermViewSet.current` answers `NO_CURRENT_TERM` and the screens
    that read it degrade to empty, which is what they already do.
    """
    from apps.audit.models import AuditAction
    from apps.audit.services import record

    previous = AcademicSession.objects.filter(is_current=True).exclude(pk=session.pk).first()
    session.is_current = True
    session.save(update_fields=["is_current", "updated_at"])

    term = _term_for_today(session)
    if term is not None and not term.is_current:
        term.is_current = True
        term.save(update_fields=["is_current", "updated_at"])

    record(
        AuditAction.SESSION_SET_CURRENT,
        actor=actor,
        obj=session,
        summary=f"{session.name} is now the current session",
        before={"session": previous.name if previous else None},
        after={"session": session.name, "term": term.name if term else None},
    )
    logger.info(
        "current session set",
        extra={"session": session.name, "term": term.name if term else None},
    )
    return session


@tenant_atomic()
def set_current_term(*, term: Term, actor=None) -> Term:
    """Advance the school to `term` — first term to second, and so on.

    Promotes the term's session too (`Term.save`), so this is also the way to
    roll over into a specific term of a new year rather than the calendar's
    pick.
    """
    from apps.audit.models import AuditAction
    from apps.audit.services import record

    previous = Term.objects.filter(is_current=True).exclude(pk=term.pk).first()
    term.is_current = True
    term.save(update_fields=["is_current", "updated_at"])

    record(
        AuditAction.TERM_SET_CURRENT,
        actor=actor,
        obj=term,
        summary=f"{term} is now the current term",
        before={"term": str(previous) if previous else None},
        after={"term": str(term), "session": term.session.name},
    )
    logger.info("current term set", extra={"term": str(term)})
    return term


# ---------------------------------------------------------------------------
# Enrolment
# ---------------------------------------------------------------------------
@tenant_atomic()
def enrol_student(
    *,
    student,
    session,
    level,
    class_arm: ClassArm | None = None,
    programme=None,
    roll_number: int | None = None,
    enforce_capacity: bool = True,
) -> Enrolment:
    """Place a student in a cohort for a session. Idempotent per session."""
    existing = Enrolment.objects.filter(student=student, session=session).first()
    if existing:
        return existing

    if class_arm is not None and enforce_capacity:
        taken = Enrolment.objects.filter(
            class_arm=class_arm, session=session, status=EnrolmentStatus.ACTIVE
        ).count()
        if taken >= class_arm.capacity:
            raise ArmFull(
                f"{class_arm} is full ({taken}/{class_arm.capacity}).",
                details={"capacity": class_arm.capacity, "enrolled": taken},
            )

    enrolment = Enrolment.objects.create(
        student=student,
        session=session,
        level=level,
        class_arm=class_arm,
        programme=programme,
        roll_number=roll_number,
        status=EnrolmentStatus.ACTIVE,
    )
    _sync_student_position(student, enrolment)
    return enrolment


def _sync_student_position(student, enrolment: Enrolment) -> None:
    """Keep the denormalised pointers on Student in step with the enrolment."""
    student.current_level = enrolment.level
    student.current_arm = enrolment.class_arm
    if enrolment.programme:
        student.programme = enrolment.programme
    student.save(update_fields=["current_level", "current_arm", "programme", "updated_at"])


# ---------------------------------------------------------------------------
# Class placement
# ---------------------------------------------------------------------------
def arm_occupancy(class_arm: ClassArm, session) -> int:
    """How many active students the class holds this session."""
    return Enrolment.objects.filter(
        class_arm=class_arm, session=session, status=EnrolmentStatus.ACTIVE
    ).count()


def next_roll_number(class_arm: ClassArm, session) -> int:
    """One past the highest number in use, so a gap left by a leaver stays a gap.

    ponytail: max+1, not a scan for the lowest free slot. Reusing a number
    would point last term's mark sheet at a different child.
    """
    highest = Enrolment.objects.filter(class_arm=class_arm, session=session).aggregate(
        highest=Max("roll_number")
    )["highest"]
    return (highest or 0) + 1


@tenant_atomic()
def assign_to_arm(
    *,
    enrolment: Enrolment,
    class_arm: ClassArm,
    roll_number: int | None = None,
    enforce_capacity: bool = True,
    actor=None,
) -> Enrolment:
    """Place — or move — a student into a class arm.

    This is the write half of class management, and it was missing.
    `enrol_student` could seat a student only at the moment the enrolment was
    created, and `apply_promotions` deliberately creates next session's
    enrolments with `class_arm=None` ("the school assigns arms itself") — so
    after every promotion an entire school sat unclassed with no code able to
    seat it. The only other route, a PATCH on the enrolment, wrote the foreign
    key and nothing else: no capacity check, and `Student.current_arm` left
    pointing at last year's class, which is the column attendance
    auto-marking, the student timetable, ID cards, SMS targeting and the
    exports all actually read.

    Transfer is the same operation as first placement, so it is the same
    function: the old seat is simply vacated by the write.
    """
    if enrolment.status != EnrolmentStatus.ACTIVE:
        raise EnrolmentNotActive(
            f"{enrolment.student} is {enrolment.get_status_display().lower()} "
            "and cannot be placed in a class.",
            details={"status": enrolment.status},
        )
    if enrolment.class_arm_id == class_arm.pk and enrolment.roll_number is not None:
        return enrolment  # already seated; moving a student to their own class is a no-op

    if not class_arm.is_active:
        raise ArmInactive(f"{class_arm} is no longer in use.")

    if class_arm.level_id != enrolment.level_id:
        raise ArmMismatch(
            f"{class_arm} is a {class_arm.level.code} class; "
            f"{enrolment.student} is enrolled in {enrolment.level.code}.",
            details={"arm_level": class_arm.level.code, "enrolment_level": enrolment.level.code},
        )

    student = enrolment.student
    if class_arm.stream_id and student.stream_id and class_arm.stream_id != student.stream_id:
        raise ArmMismatch(
            f"{class_arm} is a {class_arm.stream} class and {student} offers {student.stream}.",
            details={"arm_stream": str(class_arm.stream), "student_stream": str(student.stream)},
            code="STREAM_MISMATCH",
        )

    if enforce_capacity and enrolment.class_arm_id != class_arm.pk:
        taken = arm_occupancy(class_arm, enrolment.session)
        if taken >= class_arm.capacity:
            raise ArmFull(
                f"{class_arm} is full ({taken}/{class_arm.capacity}).",
                details={"capacity": class_arm.capacity, "enrolled": taken},
            )

    was = str(enrolment.class_arm) if enrolment.class_arm_id else None
    enrolment.class_arm = class_arm
    # A roll number is a position in one class list. Carrying last class's
    # number into the new one collides with whoever already holds it there.
    enrolment.roll_number = (
        roll_number if roll_number is not None else next_roll_number(class_arm, enrolment.session)
    )
    enrolment.save(update_fields=["class_arm", "roll_number", "updated_at"])

    # A stream is chosen by being put in a streamed class — that placement is
    # the record of the choice. Without this the student stays streamless and
    # `check_registration` refuses every subject their own class is taught.
    if class_arm.stream_id and not student.stream_id:
        student.stream = class_arm.stream
        student.save(update_fields=["stream", "updated_at"])

    # `Student.current_*` means "where this student is now". Back-filling a
    # closed session's class list must not rewind it.
    if enrolment.session.is_current:
        _sync_student_position(student, enrolment)

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        AuditAction.ARM_ASSIGNED,
        actor=actor,
        obj=enrolment,
        summary=f"{student} placed in {class_arm}",
        before={"class_arm": was},
        after={"class_arm": str(class_arm), "roll_number": enrolment.roll_number},
    )
    return enrolment


@tenant_atomic()
def allocate_to_arm(
    *,
    enrolments: list[Enrolment],
    class_arm: ClassArm,
    enforce_capacity: bool = True,
    actor=None,
) -> list[Enrolment]:
    """Seat a whole batch — the step that follows `apply_promotions`.

    All or nothing. Capacity is checked against the size of the batch before
    any of it is written, because seating thirty of forty pupils and failing on
    the thirty-first leaves the office no way to tell what happened without
    re-reading the class list.
    """
    incoming = [e for e in enrolments if e.class_arm_id != class_arm.pk]
    if enforce_capacity and incoming:
        taken = arm_occupancy(class_arm, incoming[0].session)
        if taken + len(incoming) > class_arm.capacity:
            raise ArmFull(
                f"{class_arm} holds {class_arm.capacity}; "
                f"{taken} seated and {len(incoming)} more requested.",
                details={
                    "capacity": class_arm.capacity,
                    "enrolled": taken,
                    "requested": len(incoming),
                },
            )

    return [
        assign_to_arm(enrolment=enrolment, class_arm=class_arm, enforce_capacity=False, actor=actor)
        for enrolment in enrolments
    ]


# ---------------------------------------------------------------------------
# Course / subject registration
# ---------------------------------------------------------------------------
@dataclass
class RegistrationCheck:
    subject: Subject
    ok: bool
    code: str = ""
    message: str = ""


def registered_credit_units(enrolment: Enrolment, term: Term, exclude_ids=()) -> int:
    total = (
        SubjectRegistration.objects.filter(enrolment=enrolment, term=term)
        .exclude(status__in=[RegistrationStatus.DROPPED, RegistrationStatus.REJECTED])
        .exclude(pk__in=exclude_ids)
        .aggregate(total=Sum("subject__credit_units"))["total"]
    )
    return total or 0


def unmet_prerequisites(enrolment: Enrolment, subject: Subject, term: Term) -> list[Subject]:
    """Prerequisites the student has not *passed*.

    An approved registration is not enough: sitting CSC101 and failing it does
    not qualify anyone for CSC201. A registration whose result has not been
    computed yet therefore does not count either — the exams officer computes
    the term before the next registration window opens.
    """
    required = list(subject.prerequisites.all())
    if not required:
        return []

    passed = set(
        SubjectRegistration.objects.filter(
            enrolment__student=enrolment.student,
            subject__in=required,
            status__in=[RegistrationStatus.APPROVED, RegistrationStatus.ADVISER_APPROVED],
            result__is_pass=True,
        )
        .exclude(term=term)
        .values_list("subject_id", flat=True)
    )
    return [s for s in required if s.pk not in passed]


def assert_no_prerequisite_cycle(subject: Subject, prerequisites) -> None:
    """Refuse a prerequisite list that leads back to the subject itself.

    `unmet_prerequisites` reads one level; registration then refuses the course
    until the prerequisite is passed. A loop in the catalogue — CSC201 requires
    CSC101 requires CSC201 — therefore refuses both forever, with a message
    naming a course the student cannot register either. Nothing downstream
    detects that, because from a single course's side the data looks ordinary.

    Walked breadth-first over the *stored* graph, since that is what the new
    edges are being added to: start at the proposed prerequisites and follow
    each one's own prerequisites outward. Reaching `subject` closes a loop.
    `seen` bounds it — a catalogue that already contains a cycle (keyed before
    this check existed) must not spin here.

    A subject with no primary key yet cannot be reached from anywhere, so
    creation needs no walk.
    """
    ids = {p.pk for p in prerequisites}
    if not ids or subject.pk is None:
        return

    seen: set = set()
    frontier = ids
    while frontier:
        if subject.pk in frontier:
            raise PrerequisiteCycle(
                f"{subject.code} would end up as its own prerequisite. "
                "Check what these courses already require."
            )
        seen |= frontier
        # `unlocks` is the reverse of `prerequisites`, so this reads "the
        # subjects that the frontier lists as its own prerequisites".
        frontier = set(
            Subject.objects.filter(unlocks__id__in=frontier)
            .exclude(pk__in=seen)
            .values_list("pk", flat=True)
        )


def check_registration(enrolment: Enrolment, term: Term, subject: Subject) -> RegistrationCheck:
    """Everything that can refuse one course, without writing anything.

    A refused course is re-registerable for the same reason a dropped one is:
    `approve_registration` refuses a REJECTED row with "register it again
    rather than approving it", and until this excluded it too that sentence
    named a route the student could not take — the second attempt came back
    "Already registered." for a course they were not registered for.
    """
    if (
        SubjectRegistration.objects.filter(enrolment=enrolment, subject=subject, term=term)
        .exclude(status__in=TERMINAL_REGISTRATION_STATUSES)
        .exists()
    ):
        return RegistrationCheck(subject, False, "ALREADY_REGISTERED", "Already registered.")

    if not subject.is_active:
        return RegistrationCheck(subject, False, "SUBJECT_INACTIVE", "This course is not on offer.")

    if subject.semester_offered and subject.semester_offered != term.index:
        return RegistrationCheck(
            subject,
            False,
            "WRONG_SEMESTER",
            f"{subject.code} is only offered in semester {subject.semester_offered}.",
        )

    if subject.stream_id and enrolment.student.stream_id != subject.stream_id:
        return RegistrationCheck(
            subject, False, "WRONG_STREAM", f"{subject.code} is not offered in this stream."
        )

    # A department is the one restriction the catalogue had and this did not, so
    # the screen hid an off-department course while the endpoint still took it.
    # Null on either side is not a mismatch: a course with no department is a
    # general-studies one open to everybody, and an enrolment with no programme
    # is a secondary school, where departments do not apply at all.
    if (
        subject.department_id
        and enrolment.programme_id
        and subject.department_id != enrolment.programme.department_id
    ):
        return RegistrationCheck(
            subject,
            False,
            "WRONG_DEPARTMENT",
            f"{subject.code} belongs to another department.",
        )

    missing = unmet_prerequisites(enrolment, subject, term)
    if missing:
        codes = ", ".join(s.code for s in missing)
        return RegistrationCheck(subject, False, "PREREQUISITE_NOT_MET", f"Take {codes} first.")

    return RegistrationCheck(subject, True)


@tenant_atomic()
def register_subjects(
    *,
    enrolment: Enrolment,
    term: Term,
    subjects: list[Subject],
    submit: bool = True,
    ignore_window: bool = False,
) -> list[SubjectRegistration]:
    """Register a set of courses in one transaction.

    Rejects the whole set if any course fails or the credit ceiling is
    breached — a partially registered semester is a support call.
    """
    if not ignore_window and not term.accepts_registration:
        raise RegistrationClosed()

    failures = []
    for subject in subjects:
        check = check_registration(enrolment, term, subject)
        if not check.ok:
            failures.append({"subject": subject.code, "code": check.code, "message": check.message})
    if failures:
        raise AcademicError(
            "Some courses could not be registered.",
            details={"rows": failures},
            code="REGISTRATION_REJECTED",
        )

    programme = enrolment.programme
    if programme:
        proposed = registered_credit_units(enrolment, term) + sum(s.credit_units for s in subjects)
        if proposed > programme.max_credit_units:
            raise CreditLimitExceeded(
                f"{proposed} units exceeds the {programme.max_credit_units}-unit maximum.",
                details={"proposed": proposed, "maximum": programme.max_credit_units},
            )

    status = RegistrationStatus.SUBMITTED if submit else RegistrationStatus.DRAFT
    # Secondary schools have no adviser/HOD workflow, so their registrations
    # are approved on creation.
    if programme is None:
        status = RegistrationStatus.APPROVED

    # A dropped or refused course already has a row, and
    # `unique_subject_per_term` means a second one cannot be written — a
    # `bulk_create` here raised IntegrityError, so re-registering after either
    # was a 500 rather than the retry both paths tell the student to make.
    # Revive that row instead, clearing whatever settled it.
    existing = {
        registration.subject_id: registration
        for registration in SubjectRegistration.objects.filter(
            enrolment=enrolment, term=term, subject__in=subjects
        )
    }
    registrations = []
    for subject in subjects:
        registration = existing.get(subject.pk) or SubjectRegistration(
            enrolment=enrolment, subject=subject, term=term
        )
        registration.status = status
        registration.is_carryover = _is_carryover(enrolment, subject, term=term)
        registration.dropped_at = None
        registration.rejection_reason = ""
        registration.adviser_approved_by = None
        registration.adviser_approved_at = None
        registration.hod_approved_by = None
        registration.hod_approved_at = None
        registration.save()
        registrations.append(registration)
    return registrations


def _is_carryover(enrolment: Enrolment, subject: Subject, *, term: Term) -> bool:
    """A course this student has registered before is a repeat.

    This term's own row is excluded because a revived registration is one:
    dropping a course in week two and taking it back in week three is not a
    carry-over, and counting it as one flags it on the result sheet.
    """
    return (
        SubjectRegistration.objects.filter(enrolment__student=enrolment.student, subject=subject)
        .exclude(enrolment=enrolment, term=term)
        .exists()
    )


#: Decided for good. Nothing moves a registration out of one of these — a
#: student who wants a dropped course back registers it again.
TERMINAL_REGISTRATION_STATUSES = frozenset(
    {RegistrationStatus.DROPPED, RegistrationStatus.REJECTED}
)


def assert_minimum_credits(enrolment: Enrolment, term: Term) -> None:
    programme = enrolment.programme
    if programme is None:
        return
    total = registered_credit_units(enrolment, term)
    if total < programme.min_credit_units:
        raise CreditLimitExceeded(
            f"{total} units is below the {programme.min_credit_units}-unit minimum.",
            details={"registered": total, "minimum": programme.min_credit_units},
            code="CREDIT_MINIMUM_NOT_MET",
        )


@tenant_atomic()
def approve_registration(
    *,
    registration: SubjectRegistration,
    staff,
    as_hod: bool = False,
    ignore_minimum: bool = False,
):
    """Adviser first, then HOD. Only the HOD step makes it final.

    This is where the credit *minimum* is checked, and the only place it can
    be: at entry a student is allowed to save half a semester and come back to
    it, so refusing an under-loaded set on the way in would make the draft
    impossible. `registered_credit_units` counts the whole term's load rather
    than what has been approved so far, so approving course one of eight sees
    the same total as approving course eight.

    `ignore_minimum` is for the final-year student with twelve units left to
    graduate — a real case that a rule cannot tell from an incomplete one.

    The order above was documented and unenforced, which made three things
    possible that the sentence forbids: an HOD could finalise a DRAFT the
    student never submitted, skipping the adviser entirely; either step could
    be applied to a course the student had DROPPED, resurrecting it into the
    credit count and onto the report card with `dropped_at` still set; and the
    same for a REJECTED one. Terminal is terminal — re-register instead.
    """
    if registration.status == RegistrationStatus.APPROVED:
        return registration  # already final; approving twice is not an error

    if registration.status in TERMINAL_REGISTRATION_STATUSES:
        raise RegistrationNotPending(
            f"{registration.subject.code} was {registration.get_status_display().lower()}; "
            "register it again rather than approving it.",
            details={"status": registration.status},
        )

    required = RegistrationStatus.ADVISER_APPROVED if as_hod else RegistrationStatus.SUBMITTED
    if registration.status != required:
        step = "the head of department" if as_hod else "an adviser"
        raise RegistrationNotPending(
            f"{registration.subject.code} is {registration.get_status_display().lower()} "
            f"and cannot be approved by {step} yet.",
            details={"status": registration.status, "expected": required},
        )

    if not ignore_minimum:
        assert_minimum_credits(registration.enrolment, registration.term)

    now = timezone.now()
    if as_hod:
        registration.hod_approved_by = staff
        registration.hod_approved_at = now
        registration.status = RegistrationStatus.APPROVED
    else:
        registration.adviser_approved_by = staff
        registration.adviser_approved_at = now
        registration.status = RegistrationStatus.ADVISER_APPROVED
    registration.save()
    return registration


@tenant_atomic()
def reopen_registration(*, registration: SubjectRegistration, actor=None):
    """Undo a refusal: back into the queue, not into the term.

    The one decision in this workflow a person cannot take back on their own.
    Re-registering is the documented way round a refusal, but it belongs to
    the student and only works while the add/drop window is open — which is
    exactly when it is not, because a refusal usually lands after it closes.
    So an adviser who refuses the wrong row, or refuses on a reason the
    student then answers, had nothing but a support call.

    It returns to SUBMITTED rather than to whatever it was before: refusing is
    a decision either step can take, and the adviser's own approval is not
    something an HOD's refusal should restore.

    `actor` is the `User` who undid the refusal. It is not written to the
    registration — see below — so the audit row is the only record of it, and
    the refusal reason goes in `before` because this call is what erases it.
    """
    if registration.status != RegistrationStatus.REJECTED:
        raise RegistrationNotPending(
            f"{registration.subject.code} is "
            f"{registration.get_status_display().lower()}, not refused.",
            details={"status": registration.status},
        )

    refused_reason = registration.rejection_reason
    registration.status = RegistrationStatus.SUBMITTED
    registration.rejection_reason = ""
    # Cleared, not reassigned: these two columns mean "who signed this off",
    # and reopening is not a signature. The audit row is where "who undid it"
    # belongs.
    registration.adviser_approved_by = None
    registration.adviser_approved_at = None
    registration.hod_approved_by = None
    registration.hod_approved_at = None
    registration.save(
        update_fields=[
            "status",
            "rejection_reason",
            "adviser_approved_by",
            "adviser_approved_at",
            "hod_approved_by",
            "hod_approved_at",
            "updated_at",
        ]
    )

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        AuditAction.REGISTRATION_REOPENED,
        actor=actor,
        obj=registration,
        summary=f"{registration.subject.code} reopened for {registration.enrolment.student}",
        before={"status": RegistrationStatus.REJECTED, "rejection_reason": refused_reason},
        after={"status": RegistrationStatus.SUBMITTED},
    )
    return registration


@tenant_atomic()
def reject_registration(*, registration: SubjectRegistration, staff, reason: str):
    """Refuse a course. The other half of the approval workflow.

    `RegistrationStatus.REJECTED` and `SubjectRegistration.rejection_reason`
    were declared from the start and no code could write either, while three
    selectors and `SubjectRegistration.is_active` all read them — the whole
    read side was built for a state the write side could not produce. An
    adviser could approve and could not refuse, so the only way to say no was
    to leave the registration sitting in SUBMITTED, where
    `assessment.selectors.COUNTED_STATUSES` scores it anyway.
    """
    if registration.status in TERMINAL_REGISTRATION_STATUSES:
        return registration

    was = registration.status
    registration.status = RegistrationStatus.REJECTED
    registration.rejection_reason = reason[:200]
    # Whoever refused it, recorded in the same column that would have held the
    # approval — the decision is theirs either way.
    registration.adviser_approved_by = staff
    registration.adviser_approved_at = timezone.now()
    registration.save(
        update_fields=[
            "status",
            "rejection_reason",
            "adviser_approved_by",
            "adviser_approved_at",
            "updated_at",
        ]
    )

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    # `adviser_approved_by` already names the refuser, but only until a reopen
    # clears it — the audit row is what survives that.
    record(
        AuditAction.REGISTRATION_REJECTED,
        actor=getattr(staff, "user", None),
        obj=registration,
        summary=f"{registration.subject.code} refused for {registration.enrolment.student}",
        before={"status": was},
        after={"status": RegistrationStatus.REJECTED, "rejection_reason": reason[:200]},
    )
    return registration


@tenant_atomic()
def drop_subject(*, registration: SubjectRegistration, ignore_window: bool = False, actor=None):
    """`actor` is the `User` who dropped it — a student dropping their own
    course, or a registrar doing it for them. Nothing on the row says which,
    so the audit entry is the only place that distinction is recorded."""
    if not ignore_window and not registration.term.accepts_registration:
        raise RegistrationClosed("The add/drop window has closed.")
    was = registration.status
    registration.status = RegistrationStatus.DROPPED
    registration.dropped_at = timezone.now()
    registration.save(update_fields=["status", "dropped_at", "updated_at"])

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    record(
        AuditAction.SUBJECT_DROPPED,
        actor=actor,
        obj=registration,
        summary=f"{registration.subject.code} dropped for {registration.enrolment.student}",
        before={"status": was},
        after={"status": RegistrationStatus.DROPPED},
    )
    return registration


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------
def find_clashes(entry: TimetableEntry) -> list[TimetableEntry]:
    """Overlapping periods that share a teacher, a room or a class.

    Overlap is `start < other.end AND end > other.start` — touching periods
    (10:00-11:00 and 11:00-12:00) do not clash.
    """
    conflicts = Q()
    if entry.staff_id:
        conflicts |= Q(staff_id=entry.staff_id)
    if entry.room_id:
        conflicts |= Q(room_id=entry.room_id)
    if entry.class_arm_id:
        conflicts |= Q(class_arm_id=entry.class_arm_id)
    elif entry.level_id:
        conflicts |= Q(level_id=entry.level_id, class_arm__isnull=True)
    if not conflicts:
        return []

    return list(
        TimetableEntry.objects.filter(
            Q(term=entry.term, day=entry.day)
            & Q(start_time__lt=entry.end_time, end_time__gt=entry.start_time)
            & conflicts
        )
        .exclude(pk=entry.pk)
        .select_related("subject", "staff", "room", "class_arm")
    )


def assert_no_clash(entry: TimetableEntry) -> None:
    clashes = find_clashes(entry)
    if not clashes:
        return
    raise TimetableClash(
        "That period clashes with an existing one.",
        details={
            "clashes": [
                {
                    "id": str(c.pk),
                    "subject": c.subject.code,
                    "day": c.day,
                    "start": c.start_time.strftime("%H:%M"),
                    "end": c.end_time.strftime("%H:%M"),
                    "reason": _clash_reason(entry, c),
                }
                for c in clashes
            ]
        },
    )


def _clash_reason(entry: TimetableEntry, other: TimetableEntry) -> str:
    if entry.staff_id and entry.staff_id == other.staff_id:
        return "teacher"
    if entry.room_id and entry.room_id == other.room_id:
        return "room"
    return "class"


def validate_entry(entry: TimetableEntry) -> None:
    """Everything that can refuse a period: the model's own rules, then clashes.

    Both matter and only one was reachable. The viewset checked clashes and
    called `serializer.save()`, which does not run `Model.clean()` — so the API
    accepted a period ending before it starts, and one belonging to neither a
    class arm nor a level, while the service below refused both.

    Django's `ValidationError` is translated on the way out: everything else in
    this module raises `AcademicError`, and an untranslated one reaches DRF as
    an unhandled exception — a 500 for what is a 400.
    """
    try:
        entry.full_clean(exclude=["id"])
    except DjangoValidationError as exc:
        raise InvalidPeriod(
            "; ".join(exc.messages),
            details=exc.message_dict if hasattr(exc, "error_dict") else {},
        ) from exc
    assert_no_clash(entry)


@tenant_atomic()
def create_timetable_entry(**fields) -> TimetableEntry:
    entry = TimetableEntry(**fields)
    validate_entry(entry)
    entry.save()
    return entry
