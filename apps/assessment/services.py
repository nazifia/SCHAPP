"""Score entry, result computation, publication and promotion.

Three jobs, in the order a term actually happens:

1. **entry** — a teacher submits a whole class in one transactional request,
   with the same version check attendance uses, because the same teacher is on
   the same 3G connection;
2. **computation** — scores become grades, positions, GPAs and CGPAs. It is a
   rebuild, not an increment: recomputing a term twice gives the same answer,
   which is the only property that makes a correction safe two weeks later;
3. **publication and promotion** — publishing is what makes a result visible
   to a parent, and it is refused while any mark is missing.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.utils import timezone

from apps.academics.models import Enrolment, EnrolmentStatus
from apps.api.bulk import BulkOutcome, RowError, atomic_bulk
from apps.api.exceptions import AppError
from apps.tenants.db import tenant_atomic

from .grading import ZERO, average, band_for, grade_point_average, percentage, quantise, rank
from .models import (
    PromotionDecision,
    Score,
    SubjectResult,
    TermResult,
)
from .selectors import (
    cohort_key,
    components_for,
    counted_registrations,
    scale_for,
)

logger = logging.getLogger(__name__)


class AssessmentError(AppError):
    default_code = "ASSESSMENT_ERROR"


class ScoreEntryClosed(AssessmentError):
    default_code = "SCORE_ENTRY_CLOSED"
    default_detail = "Score entry is closed for this term."


class ResultsAlreadyPublished(AssessmentError):
    default_code = "RESULTS_PUBLISHED"
    default_detail = "Results for this term are published and can no longer be edited."


class ResultsIncomplete(AssessmentError):
    default_code = "RESULTS_INCOMPLETE"
    default_detail = "Some subjects are still missing marks."


class NoGradingScale(AssessmentError):
    default_code = "NO_GRADING_SCALE"
    default_detail = "No grading scale is configured for this class."


# ---------------------------------------------------------------------------
# Score entry
# ---------------------------------------------------------------------------
@dataclass
class ScoreRow:
    student_id: str
    #: None means "remove whatever is stored here" — the teacher cleared the
    #: field, which is an unmark and not a zero.
    score: Decimal | None
    #: Version the client last saw. None means "I am creating this".
    version: int | None = None
    #: When the teacher keyed it in offline; may predate the sync by hours.
    recorded_at: datetime | None = None


def _conflict(outcome: BulkOutcome, index: int, row: ScoreRow, stored) -> bool:
    """True when the device's version is stale, with the row error recorded."""
    if row.version is None or row.version == stored.version:
        return False
    outcome.errors.append(
        RowError(
            index,
            row.student_id,
            "CONFLICT",
            f"Changed on the server since you last synced (now {stored.score:g}).",
        )
    )
    return True


def enter_scores(
    *,
    term,
    subject,
    component,
    rows: list[ScoreRow],
    entered_by=None,
    allowed_student_ids: set | None = None,
    override_window: bool = False,
) -> BulkOutcome:
    """One component, one subject, a whole class, one transaction.

    Refused wholesale only for a closed window or published results — those
    are decisions about the term. Everything else is refused per row, so a
    stale class list on a device names the pupil it is wrong about instead of
    failing forty marks with one message.
    """
    if term.results_published_at is not None and not override_window:
        raise ResultsAlreadyPublished()
    if not override_window and not term.accepts_scores:
        raise ScoreEntryClosed()

    def handler(outcome: BulkOutcome) -> None:
        student_ids = [row.student_id for row in rows]
        registrations = {
            str(registration.enrolment.student_id): registration
            for registration in counted_registrations(
                term, subject=subject, enrolment__student_id__in=student_ids
            )
        }
        existing = {
            str(score.registration_id): score
            for score in Score.objects.select_for_update().filter(
                component=component, registration__in=list(registrations.values())
            )
        }

        for index, row in enumerate(rows):
            if allowed_student_ids is not None and row.student_id not in allowed_student_ids:
                outcome.errors.append(
                    RowError(
                        index,
                        row.student_id,
                        "NOT_IN_CLASS",
                        "This student is not in the class you are marking.",
                    )
                )
                continue

            registration = registrations.get(str(row.student_id))
            if registration is None:
                outcome.errors.append(
                    RowError(
                        index,
                        row.student_id,
                        "NOT_REGISTERED",
                        f"This student is not registered for {subject.code} this term.",
                    )
                )
                continue

            score = existing.get(str(registration.pk))

            # An unmark: the mark sheet field was cleared. Versioned like an
            # edit, because removing an office correction unseen is the same
            # accident as overwriting one.
            if row.score is None:
                if score is None:
                    continue  # nothing stored, nothing to remove
                if _conflict(outcome, index, row, score):
                    continue
                score.delete()
                outcome.deleted += 1
                continue

            value = quantise(row.score)
            if value < ZERO or value > component.max_score:
                outcome.errors.append(
                    RowError(
                        index,
                        row.student_id,
                        "SCORE_OUT_OF_RANGE",
                        f"{component.code} is marked out of {component.max_score:g}.",
                    )
                )
                continue

            if score is None:
                Score.objects.create(
                    registration=registration,
                    component=component,
                    score=value,
                    entered_by=entered_by,
                    recorded_at=row.recorded_at or timezone.now(),
                )
                outcome.created += 1
                continue

            if _conflict(outcome, index, row, score):
                continue

            score.score = value
            score.entered_by = entered_by
            score.recorded_at = row.recorded_at or score.recorded_at
            score.version += 1
            score.save(
                update_fields=["score", "entered_by", "recorded_at", "version", "updated_at"]
            )
            outcome.updated += 1

    return atomic_bulk(handler)


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------
def _score_subject(registration, *, components, scale) -> dict:
    """Totals and grade for one registration, without touching the database."""
    marks = {str(score.component_id): score.score for score in registration.scores.all()}
    max_total = sum((c.max_score for c in components), ZERO)
    total = sum((marks.get(str(c.pk), ZERO) for c in components), ZERO)
    pct = percentage(total, max_total)

    band = band_for(scale.bands(), pct) if scale else None
    return {
        "total_score": quantise(total),
        "max_total": quantise(max_total),
        "percentage": pct,
        "grade": band.letter if band else "",
        "grade_point": band.point if band else ZERO,
        "teacher_remark": band.remark if band else "",
        "credit_units": registration.subject.credit_units,
        "is_pass": bool(scale) and pct >= scale.pass_percentage,
        # A subject with no components configured can never be complete, which
        # is what stops an unconfigured subject being published as a zero.
        "is_complete": bool(components) and all(str(c.pk) in marks for c in components),
    }


@tenant_atomic()
def recompute_term(term) -> dict:
    """Rebuild every derived row for a term. Idempotent.

    Ordering matters: subject results first (they need only the scores), then
    positions (they need the whole cohort's subject results), then term
    results (they need the subject results), then term positions.
    """
    # The whole term, always. Recomputing one student would rank them against
    # a subset of their own class, which is worse than recomputing too much.
    registrations = list(counted_registrations(term).prefetch_related("scores"))

    component_cache: dict = {}
    scale_cache: dict = {}
    computed: list[tuple] = []

    for registration in registrations:
        level = registration.enrolment.level
        cache_key = (registration.subject_id, registration.enrolment.level_id)
        if cache_key not in component_cache:
            component_cache[cache_key] = components_for(subject=registration.subject, level=level)
        if registration.enrolment.level_id not in scale_cache:
            scale_cache[registration.enrolment.level_id] = scale_for(level)

        values = _score_subject(
            registration,
            components=component_cache[cache_key],
            scale=scale_cache[registration.enrolment.level_id],
        )
        computed.append((registration, values))

    _apply_subject_positions(computed)

    # ponytail: update_or_create per row — a term is a few thousand rows and
    # this runs from a Celery task, not a request. Batch it if it ever shows up
    # in a profile.
    results: dict = {}
    enrolments_seen: dict = {}
    for registration, values in computed:
        result, _ = SubjectResult.objects.update_or_create(
            registration=registration, defaults=values
        )
        results.setdefault(str(registration.enrolment_id), []).append(result)
        enrolments_seen[str(registration.enrolment_id)] = registration.enrolment

    term_results = _build_term_results(term, results, enrolments_seen, scale_cache)
    _apply_term_positions(term_results)
    dropped = _prune_stale_results(term, registrations, enrolments_seen)

    logger.info(
        "term recomputed",
        extra={
            "term": str(term),
            "subjects": len(computed),
            "students": len(term_results),
            "pruned": dropped,
        },
    )
    return {
        "subject_results": len(computed),
        "term_results": len(term_results),
        "pruned": dropped,
    }


def _prune_stale_results(term, registrations, enrolments_seen: dict) -> int:
    """Delete derived rows whose source no longer counts.

    "A rebuild, not an increment" was only ever half true: every run wrote the
    rows it computed and no run removed the ones it did not. `drop_subject` and
    `reject_registration` move a registration out of `COUNTED_STATUSES`, and the
    `SubjectResult` it had already earned simply stayed:

    * `broadsheet` and `report_card_context` filter on `registration__term`
      alone, so a dropped course kept printing on the report card — and
      `transcript_context`'s docstring promised the opposite in as many words
      ("Withdrawn or dropped courses never appear, because they never produced
      a SubjectResult");
    * `publish_results` counts `is_complete=False` over the same unfiltered
      set, so an unfinished result for a dropped course blocked publication
      permanently. There was no way out: `enter_scores` refuses the very
      registration with `NOT_REGISTERED`, so the marks could never be
      completed and `force` was the only exit.

    A `TermResult` goes when the enrolment has no counted registration left at
    all — otherwise a pupil who dropped everything kept an average, a position
    and a promotion decision that `results_visible_to` showed to their parent.
    """
    live_registrations = [registration.pk for registration in registrations]
    stale_subjects = SubjectResult.objects.filter(registration__term=term).exclude(
        registration_id__in=live_registrations
    )
    stale_terms = TermResult.objects.filter(term=term).exclude(
        enrolment_id__in=[enrolment.pk for enrolment in enrolments_seen.values()]
    )
    return stale_subjects.delete()[0] + stale_terms.delete()[0]


def _apply_subject_positions(computed: list[tuple]) -> None:
    """Rank each subject within its cohort and attach the class statistics."""
    groups: dict = {}
    for registration, values in computed:
        key = (cohort_key(registration.enrolment), registration.subject_id)
        groups.setdefault(key, []).append(values)

    for members in groups.values():
        percentages = [m["percentage"] for m in members]
        positions = rank(percentages)
        stats = {
            "cohort_size": len(members),
            "class_average": average(percentages),
            "class_highest": max(percentages),
            "class_lowest": min(percentages),
        }
        for member, position in zip(members, positions, strict=True):
            member["position"] = position
            member.update(stats)


def _build_term_results(
    term, results: dict, enrolments: dict, scale_cache: dict
) -> list[TermResult]:
    built = []
    for enrolment_id, subject_results in results.items():
        enrolment = enrolments[enrolment_id]
        scale = scale_cache.get(enrolment.level_id)

        percentages = [r.percentage for r in subject_results]
        gpa = None
        if scale is not None and scale.uses_grade_points:
            gpa = grade_point_average((r.grade_point, r.credit_units) for r in subject_results)

        present, total_days = _attendance_totals(enrolment.student, term)
        term_result, _ = TermResult.objects.update_or_create(
            enrolment=enrolment,
            term=term,
            defaults={
                "subjects_count": len(subject_results),
                "total_score": quantise(sum((r.total_score for r in subject_results), ZERO)),
                "max_total": quantise(sum((r.max_total for r in subject_results), ZERO)),
                "average": average(percentages),
                "credit_units_registered": sum(r.credit_units for r in subject_results),
                "credit_units_earned": sum(r.credit_units for r in subject_results if r.is_pass),
                "gpa": gpa,
                "days_present": present,
                "days_total": total_days,
            },
        )
        term_result.cgpa = _cumulative_gpa(enrolment.student, term_result)
        term_result.save(update_fields=["cgpa", "updated_at"])
        built.append(term_result)
    return built


def _attendance_totals(student, term) -> tuple[int, int]:
    """The daily register only — per-period rows would count a day many times."""
    from apps.attendance.models import AttendanceStatus, StudentAttendance

    records = StudentAttendance.objects.filter(student=student, term=term, subject__isnull=True)
    total = records.count()
    present = records.filter(status__in=[AttendanceStatus.PRESENT, AttendanceStatus.LATE]).count()
    return present, total


def _cumulative_gpa(student, term_result: TermResult) -> Decimal | None:
    """CGPA over every term up to and including this one.

    Weighted by units registered, which is what a Nigerian polytechnic calls
    TNU. Secondary schools have no credit units, so this stays null and the
    report card prints the average instead.
    """
    history = list(
        TermResult.objects.filter(enrolment__student=student, gpa__isnull=False)
        .select_related("term", "term__session")
        .exclude(pk=term_result.pk)
    )
    history.append(term_result)
    history.sort(key=_term_order)

    cutoff = _term_order(term_result)
    entries = [
        (row.gpa, row.credit_units_registered)
        for row in history
        if _term_order(row) <= cutoff and row.gpa is not None
    ]
    return grade_point_average(entries)


def _term_order(result: TermResult) -> tuple[date, int]:
    """Where a term sits in time: session start, then index within the session.

    `start_date` is coerced because a DateField assigned a string keeps that
    string until the instance is reloaded — comparing one of those against a
    date loaded from the database raises TypeError, and `term_result` is
    routinely an object the caller built rather than one read back.
    """
    start = result.term.session.start_date
    if isinstance(start, str):
        start = date.fromisoformat(start)
    return (start, result.term.index)


def _apply_term_positions(term_results: list[TermResult]) -> None:
    groups: dict = {}
    for result in term_results:
        groups.setdefault(cohort_key(result.enrolment), []).append(result)

    for members in groups.values():
        positions = rank([m.average for m in members])
        for member, position in zip(members, positions, strict=True):
            member.position = position
            member.cohort_size = len(members)
            member.save(update_fields=["position", "cohort_size", "updated_at"])


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------
@tenant_atomic()
def publish_results(term, *, actor=None, force: bool = False):
    """Make a term's results visible to students and guardians.

    Refused while any counted registration is missing a mark. `force` exists
    for the real case where a subject genuinely has no exam — the override is
    audited, so it is a decision someone owns rather than a silent gap.
    """
    registrations = counted_registrations(term)
    missing = registrations.filter(result__isnull=True).count()
    incomplete = SubjectResult.objects.filter(registration__term=term, is_complete=False).count()

    if (missing or incomplete) and not force:
        raise ResultsIncomplete(
            "Results cannot be published while marks are missing.",
            details={"not_computed": missing, "incomplete": incomplete},
        )

    term.results_published_at = timezone.now()
    term.save(update_fields=["results_published_at", "updated_at"])
    _audit("assessment.results.published", term, actor, forced=force)
    return term


@tenant_atomic()
def unpublish_results(term, *, actor=None):
    term.results_published_at = None
    term.save(update_fields=["results_published_at", "updated_at"])
    _audit("assessment.results.unpublished", term, actor)
    return term


def _audit(action: str, obj, actor, **extra) -> None:
    from apps.audit.services import record

    record(action, actor=actor, obj=obj, summary=str(obj)[:255], after=extra or None)


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------
#: Below this CGPA a tertiary student repeats the level. 1.50 is the common
#: Nigerian polytechnic/university probation line; override per call.
MIN_CGPA = Decimal("1.50")


def _session_average(enrolment: Enrolment) -> Decimal | None:
    """A student's average across every computed term of their session."""
    averages = list(
        TermResult.objects.filter(
            enrolment=enrolment, term__session_id=enrolment.session_id
        ).values_list("average", flat=True)
    )
    if not averages:
        return None
    return average(averages)


def _final_semester_cgpa(enrolment: Enrolment) -> Decimal | None:
    """The CGPA standing at the end of the session's last computed semester.

    A tertiary student moves up on that standing, not on the mean of the two
    semesters' percentages: the CGPA is already unit-weighted and already
    carries earlier sessions, which is what a carry-over student's level
    actually depends on. Null for a secondary school, where no scale awards
    grade points — and that null is the signal to fall back to percentages.
    """
    last = (
        TermResult.objects.filter(enrolment=enrolment, term__session_id=enrolment.session_id)
        .order_by("-term__index")
        .first()
    )
    return last.cgpa if last else None


@tenant_atomic()
def decide_promotions(
    *, session, pass_percentage: Decimal | None = None, min_cgpa: Decimal | None = None
) -> list[dict]:
    """Work out who moves up, who repeats and who graduates.

    Decides only — nothing changes class until `apply_promotions` runs, so an
    exams officer can review the list and override individual rows first.
    """
    decisions = []
    enrolments = (
        Enrolment.objects.filter(session=session, status=EnrolmentStatus.ACTIVE)
        .select_related("student", "level", "level__next_level")
        .order_by("student__last_name")
    )

    for enrolment in enrolments:
        mean = _session_average(enrolment)
        if mean is None:
            continue  # no results computed for this student; nothing to decide on

        cgpa = _final_semester_cgpa(enrolment)
        if cgpa is not None:
            threshold = min_cgpa if min_cgpa is not None else MIN_CGPA
            passed = cgpa >= threshold
            standing, bar = f"CGPA {cgpa:g}", f"{threshold:g}"
        else:
            scale = scale_for(enrolment.level)
            threshold = (
                pass_percentage
                if pass_percentage is not None
                else (scale.pass_percentage if scale else Decimal("40.00"))
            )
            passed = mean >= threshold
            standing, bar = f"session average {mean:g}%", f"{threshold:g}%"

        if not passed:
            decision = PromotionDecision.REPEAT
            note = f"Repeats {enrolment.level.code}: {standing} below {bar}."
        elif enrolment.level.is_terminal:
            decision = PromotionDecision.GRADUATE
            note = f"Graduates from {enrolment.level.code} with {standing}."
        elif enrolment.level.next_level is None:
            decision = PromotionDecision.REPEAT
            note = f"No level follows {enrolment.level.code}; held pending a structure fix."
        else:
            decision = PromotionDecision.PROMOTE
            note = f"Promoted to {enrolment.level.next_level.code} with {standing}."

        _record_decision(enrolment, decision, note)
        decisions.append(
            {
                "enrolment": str(enrolment.pk),
                "student": enrolment.student.full_name,
                "average": mean,
                "cgpa": cgpa,
                "decision": decision,
                "note": note,
            }
        )
    return decisions


def _record_decision(enrolment: Enrolment, decision: str, note: str) -> None:
    """The decision lives on the last term result — that is what gets printed."""
    last = TermResult.objects.filter(enrolment=enrolment).order_by("-term__index").first()
    if last is not None:
        last.promotion_status = decision
        last.save(update_fields=["promotion_status", "updated_at"])
    enrolment.promotion_note = note[:200]
    enrolment.save(update_fields=["promotion_note", "updated_at"])


@tenant_atomic()
def override_promotion(*, term_result: TermResult, decision: str, reason: str, actor=None):
    """An exams officer's word beats the engine, but it is written down."""
    term_result.promotion_status = decision
    term_result.save(update_fields=["promotion_status", "updated_at"])
    enrolment = term_result.enrolment
    enrolment.promotion_note = f"Override: {decision}. {reason}"[:200]
    enrolment.save(update_fields=["promotion_note", "updated_at"])
    _audit("assessment.promotion.overridden", term_result, actor, decision=decision, reason=reason)
    return term_result


@tenant_atomic()
def apply_promotions(*, session, next_session, actor=None) -> dict:
    """Move the decisions into next session's enrolments.

    Idempotent: `enrol_student` returns the existing row for a session, so
    running this twice does not double-enrol anybody.
    """
    from apps.academics.services import enrol_student
    from apps.people.models import StudentStatus

    counts = {"promoted": 0, "repeated": 0, "graduated": 0, "skipped": 0}
    enrolments = (
        Enrolment.objects.filter(session=session, status=EnrolmentStatus.ACTIVE)
        .select_related("student", "level", "level__next_level", "programme")
        .order_by("student__last_name")
    )

    for enrolment in enrolments:
        last = TermResult.objects.filter(enrolment=enrolment).order_by("-term__index").first()
        decision = last.promotion_status if last else PromotionDecision.PENDING

        if decision == PromotionDecision.PROMOTE and enrolment.level.next_level is not None:
            enrolment.status = EnrolmentStatus.PROMOTED
            enrol_student(
                student=enrolment.student,
                session=next_session,
                level=enrolment.level.next_level,
                programme=enrolment.programme,
                # The school assigns arms itself; guessing one would put a
                # pupil in a stream they never chose.
                class_arm=None,
                enforce_capacity=False,
            )
            counts["promoted"] += 1
        elif decision == PromotionDecision.REPEAT:
            enrolment.status = EnrolmentStatus.REPEATED
            enrol_student(
                student=enrolment.student,
                session=next_session,
                level=enrolment.level,
                programme=enrolment.programme,
                class_arm=None,
                enforce_capacity=False,
            )
            counts["repeated"] += 1
        elif decision == PromotionDecision.GRADUATE:
            enrolment.status = EnrolmentStatus.GRADUATED
            student = enrolment.student
            student.status = StudentStatus.GRADUATED
            student.date_left = student.date_left or timezone.now().date()
            student.save(update_fields=["status", "date_left", "updated_at"])
            counts["graduated"] += 1
        else:
            counts["skipped"] += 1
            continue

        enrolment.save(update_fields=["status", "updated_at"])

    _audit("assessment.promotion.applied", session, actor, **counts)
    logger.info("promotions applied", extra={"session": str(session), **counts})
    return counts
