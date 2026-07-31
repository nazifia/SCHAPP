"""Admissions pipeline and person record creation."""

import logging
import secrets
from datetime import date

from django.utils import timezone

from apps.api.exceptions import AppError
from apps.numbering.msisdn import InvalidMsisdn, normalize
from apps.tenants.db import tenant_atomic

from .models import (
    Application,
    ApplicationStatus,
    Guardian,
    Staff,
    StaffStatus,
    Student,
    StudentGuardian,
    StudentStatus,
)
from .numbering import (
    DEFAULT_ADMISSION_FORMAT,
    DEFAULT_MATRIC_FORMAT,
    DEFAULT_STAFF_FORMAT,
    NumberContext,
    NumberFormatError,
    next_number,
)

logger = logging.getLogger(__name__)


class AdmissionError(AppError):
    default_code = "ADMISSION_ERROR"


class InvalidTransition(AdmissionError):
    default_code = "INVALID_TRANSITION"


class NumberUnavailable(AdmissionError):
    """The configured format needs something this record has not got."""

    default_code = "NUMBER_FORMAT_UNSATISFIED"


class PlanLimitReached(AppError):
    """The school is at the seat count its subscription plan allows."""

    #: 402, not 403: nothing is wrong with the caller's permissions, and the
    #: fix is a plan change rather than a different account. The client
    #: branches on the code, so the status is for the humans reading logs.
    status_code = 402
    default_code = "PLAN_LIMIT_REACHED"


#: Deliberately strict. Skipping screening or accepting a rejected offer are
#: the two mistakes that produce ghost students in a class list.
ALLOWED_TRANSITIONS = {
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.SCREENING,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.SCREENING: {
        ApplicationStatus.OFFERED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.OFFERED: {
        ApplicationStatus.ACCEPTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.ACCEPTED: {ApplicationStatus.ENROLLED, ApplicationStatus.WITHDRAWN},
    ApplicationStatus.ENROLLED: set(),
    ApplicationStatus.REJECTED: set(),
    ApplicationStatus.WITHDRAWN: set(),
}


def _allocate(model, field: str, fmt: str, context: NumberContext) -> str:
    """`next_number`, but a format the record cannot satisfy is a 400, not a 500."""
    try:
        return next_number(model, field, fmt, context)
    except NumberFormatError as exc:
        raise NumberUnavailable(str(exc)) from exc


def _number_formats(tenant):
    config = getattr(tenant, "configuration", None)
    overrides = getattr(config, "label_overrides", None) or {}
    return {
        "admission": overrides.get("ADMISSION_NUMBER_FORMAT", DEFAULT_ADMISSION_FORMAT),
        "matric": overrides.get("MATRIC_NUMBER_FORMAT", DEFAULT_MATRIC_FORMAT),
        "staff": overrides.get("STAFF_NUMBER_FORMAT", DEFAULT_STAFF_FORMAT),
    }


def _assert_nin_if_required(tenant, fields: dict) -> None:
    """`TenantConfiguration.require_nin` is a switch a school turns on.

    It was stored and read by nothing, which is worse than not offering it: an
    administrator ticks the box and believes the records are complete. Checked
    in the service rather than the serializer so the CSV import and the
    admissions conversion obey it too.
    """
    config = getattr(tenant, "configuration", None)
    if getattr(config, "require_nin", False) and not (fields.get("nin") or "").strip():
        raise AdmissionError(
            "This institution requires a NIN on every record.",
            code="NIN_REQUIRED",
        )


#: Statuses that do not occupy a seat. A school that has been running for ten
#: years has more alumni than pupils, and billing them for both would make
#: every plan limit meaningless within a few sessions.
_FREED_STUDENT_STATUSES = {
    StudentStatus.GRADUATED,
    StudentStatus.WITHDRAWN,
    StudentStatus.TRANSFERRED,
}
_FREED_STAFF_STATUSES = {StaffStatus.EXITED}


def _assert_seat_available(tenant, model, freed: set, limit_field: str, noun: str) -> None:
    """`Plan.max_students` / `Plan.max_staff`, which nothing enforced.

    Both are serialised straight to the pricing screen by
    `apps.tenants.serializers`, so a school is shown a seat count as a promise
    while the API let it enrol without bound — the `Plan` docstring's claim
    that "limits are enforced at the service layer" was the whole enforcement.

    Checked here rather than in the serializer for the same reason the NIN
    check is: the CSV import and the admissions conversion both come through
    `create_student` and would otherwise walk straight past it.

    ponytail: counts on every create, which is one indexed COUNT per new
    record. Cache it against the tenant if a bulk import of four hundred
    pupils ever shows up in a profile.
    """
    plan = getattr(tenant, "plan", None)
    if plan is None:  # no subscription yet — every tenant, between signup and billing
        return
    limit = getattr(plan, limit_field, None)
    if limit is None:  # the documented "unlimited"
        return

    in_use = model.objects.alive().exclude(status__in=freed).count()
    if in_use >= limit:
        raise PlanLimitReached(
            f"The {plan.name} plan covers {limit} {noun}. Upgrade the subscription to add more.",
            details={"limit": limit, "in_use": in_use, "plan": plan.code},
        )


def generate_reference() -> str:
    """Applicant-facing reference. Random, not sequential: a guessable one
    lets an applicant read someone else's status page."""
    return f"APP-{timezone.now():%y}-{secrets.token_hex(3).upper()}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@tenant_atomic()
def create_student(*, tenant=None, admission_number: str = "", **fields) -> Student:
    _assert_nin_if_required(tenant, fields)
    _assert_seat_available(tenant, Student, _FREED_STUDENT_STATUSES, "max_students", "students")
    if not admission_number:
        context = NumberContext(
            year=(fields.get("date_admitted") or date.today()).year,
            dept=getattr(fields.get("programme"), "code", "") or "",
            prog=getattr(fields.get("programme"), "code", "") or "",
            level=getattr(fields.get("current_level"), "code", "") or "",
        )
        admission_number = _allocate(
            Student, "admission_number", _number_formats(tenant)["admission"], context
        )
    return Student.objects.create(admission_number=admission_number, **fields)


@tenant_atomic()
def assign_matriculation_number(student: Student, *, tenant=None) -> Student:
    """Issued once, at matriculation. Never reissued — it is on the transcript."""
    if student.matriculation_number:
        return student

    department = getattr(student.programme, "department", None)
    context = NumberContext(
        year=(student.date_admitted or date.today()).year,
        dept=getattr(department, "code", "") or "",
        prog=getattr(student.programme, "code", "") or "",
        level=getattr(student.current_level, "code", "") or "",
    )
    student.matriculation_number = _allocate(
        Student, "matriculation_number", _number_formats(tenant)["matric"], context
    )
    student.save(update_fields=["matriculation_number", "updated_at"])
    return student


@tenant_atomic()
def create_staff(*, tenant=None, staff_number: str = "", **fields) -> Staff:
    _assert_nin_if_required(tenant, fields)
    _assert_seat_available(tenant, Staff, _FREED_STAFF_STATUSES, "max_staff", "staff")
    if not staff_number:
        context = NumberContext(
            year=(fields.get("date_employed") or date.today()).year,
            dept=getattr(fields.get("department"), "code", "") or "",
        )
        staff_number = _allocate(Staff, "staff_number", _number_formats(tenant)["staff"], context)
    return Staff.objects.create(staff_number=staff_number, **fields)


@tenant_atomic()
def link_staff_account(staff: Staff, *, roles: list[str] | None = None, request=None):
    """Give a staff record a login, matched on their phone number.

    HR creating a `Staff` row and an administrator creating a `User` were two
    unconnected acts, so a teacher onboarded through the staff screen had a
    personnel file and no way in — and `staff_for()`, which every "my classes",
    "my register" and admissions decision reads, found nothing. This is the one
    step that joins them.

    Matched on phone rather than created blind: a teacher who is also a parent
    at the school already has an account, and a second one would split their
    wards from their classes.

    No credential is set here. The holder signs in with an OTP to their own
    phone; an administrator who could set someone's PIN could sign in as them.
    """
    from apps.accounts import services as accounts

    if not staff.phone:
        raise AdmissionError(
            f"{staff.full_name} has no phone number, so there is nothing to sign in with.",
            code="STAFF_HAS_NO_PHONE",
        )

    try:
        user, _ = accounts.get_or_create_user(
            staff.phone,
            first_name=staff.first_name,
            last_name=staff.last_name,
            other_name=staff.other_name,
            email=staff.email,
        )
    except InvalidMsisdn as exc:
        raise AdmissionError(exc.message, code=exc.code) from exc

    other = Staff.objects.alive().filter(user=user).exclude(pk=staff.pk).first()
    if other is not None:
        raise AdmissionError(
            f"That number already signs in as {other.full_name} ({other.staff_number}).",
            code="ACCOUNT_IN_USE",
            details={"staff_number": other.staff_number},
        )

    if staff.user_id != user.pk:
        staff.user = user
        staff.save(update_fields=["user", "updated_at"])

    if roles:
        accounts.set_roles(user, roles, request=request)
    return user


#: A pupil leaving, and what that makes of their live cohort membership.
#: `SUSPENDED` is deliberately absent: a suspended pupil is still on the roll,
#: still counts against the class and is still billed — the suspension ends.
#: Annotated `str` keys, not `StudentStatus`: callers hand this whatever came
#: off the wire, and a serializer gives a plain string.
_STUDENT_EXIT_TO_ENROLMENT: dict[str, str] = {
    StudentStatus.GRADUATED: "GRADUATED",
    StudentStatus.WITHDRAWN: "WITHDRAWN",
    StudentStatus.TRANSFERRED: "TRANSFERRED",
}

#: Departures a pupil can be brought back from by flipping the status alone. A
#: graduate who returns is a fresh admission, not a reinstatement — the same
#: reasoning that stops `set_staff_status` restoring roles automatically.
_REINSTATABLE: set[str] = {"WITHDRAWN", "TRANSFERRED"}


@tenant_atomic()
def set_student_status(student: Student, status: str, *, previous: str | None = None) -> Student:
    """Change a pupil's status, and carry their enrolment with it.

    The mirror of `set_staff_status`, and it was missing. `Student.status` was
    writable straight through the serializer, so a registrar marking a leaver
    WITHDRAWN or TRANSFERRED left their `Enrolment` on ACTIVE — and ACTIVE
    enrolment is what four separate queries mean by "on the roll":

    * `finance.services.cohort_for` bills them for next term;
    * `assessment.services.decide_promotions` decides their promotion, and
      `apply_promotions` re-enrols them into the next session;
    * `academics.services.enrol_student` counts them against class capacity,
      so a departed pupil holds a seat nobody can fill;
    * `EnrolmentStatus.WITHDRAWN` and `TRANSFERRED` were declared and no code
      ever wrote either.

    Only ACTIVE enrolments are touched. PROMOTED and REPEATED rows are the
    history of past sessions and closing those would rewrite it.
    """
    from apps.academics.models import Enrolment, EnrolmentStatus

    before = student.status if previous is None else previous
    student.status = status

    closing = _STUDENT_EXIT_TO_ENROLMENT.get(status)
    live = Enrolment.objects.filter(student=student, status=EnrolmentStatus.ACTIVE)

    if closing:
        if student.date_left is None:
            student.date_left = timezone.now().date()
        live.update(status=closing, updated_at=timezone.now())
    elif status == StudentStatus.ACTIVE and _STUDENT_EXIT_TO_ENROLMENT.get(before):
        student.date_left = None
        # Reopening bypasses the class-arm capacity check on purpose: this pupil
        # is taking back the seat they already held, not claiming a new one.
        #
        # Current session only. Unscoped, this reinstated *every* enrolment the
        # pupil had ever left — a child who withdrew in 2023/24, came back, and
        # withdrew again in 2025/26 was returned to both cohorts at once, and an
        # ACTIVE enrolment in a closed session is what `cohort_for` bills and
        # `decide_promotions` decides on. Closing is unscoped on purpose (every
        # live membership ends when a pupil leaves); reopening is not the
        # mirror of that, because only the last one was live.
        Enrolment.objects.filter(
            student=student, status__in=_REINSTATABLE, session__is_current=True
        ).update(status=EnrolmentStatus.ACTIVE, updated_at=timezone.now())

    student.save(update_fields=["status", "date_left", "updated_at"])
    return student


@tenant_atomic()
def set_staff_status(
    staff: Staff, status: str, *, previous: str | None = None, reason: str = ""
) -> Staff:
    """Change employment status, and carry the login with it.

    An exit that leaves the account alive is the gap that matters: the teacher
    hands back the keys and still holds a session that marks a register.
    Reinstatement is the same door in reverse — but the *roles* are not
    restored automatically, because someone returning in a different post
    should be granted that post deliberately.

    Reopening a login is conditioned on the staff member having actually been
    exited, not merely on being active now: an account disabled for some other
    reason must not be quietly reopened by an unrelated edit to a personnel
    file. `previous` is for callers that have already written the new status.
    """
    was_exited = (staff.status if previous is None else previous) == StaffStatus.EXITED
    staff.status = status
    if status == StaffStatus.EXITED and staff.date_exited is None:
        staff.date_exited = timezone.now().date()
    if status != StaffStatus.EXITED and was_exited:
        staff.date_exited = None
    staff.save(update_fields=["status", "date_exited", "updated_at"])

    user = staff.user
    if user is None:
        return staff

    if status == StaffStatus.EXITED and user.is_active:
        user.is_active = False
        user.save(update_fields=["is_active", "updated_at"])
        # Authentication already refuses an inactive user; the refresh families
        # are the part that would otherwise survive and hand every old device
        # its session back on reinstatement.
        from apps.auth_phone import tokens

        tokens.revoke_all_for_user(user, reason or "staff exited")
    elif status != StaffStatus.EXITED and was_exited and not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active", "updated_at"])
    return staff


@tenant_atomic()
def link_guardian(
    *,
    student: Student,
    phone: str,
    relationship: str = "GUARDIAN",
    is_primary: bool = False,
    **guardian_fields,
) -> StudentGuardian:
    """Attach a guardian, matching on phone so one parent is one record.

    This is what makes "one login, several children" work: the second child's
    form finds the existing guardian instead of creating a twin.
    """
    try:
        canonical = normalize(phone)
    except InvalidMsisdn as exc:
        raise AdmissionError(exc.message, code=exc.code) from exc

    guardian, created = Guardian.objects.get_or_create(phone=canonical, defaults=guardian_fields)
    if not created:
        # Fill blanks from the new form without overwriting known-good data.
        changed = []
        for field, value in guardian_fields.items():
            if value and not getattr(guardian, field, None):
                setattr(guardian, field, value)
                changed.append(field)
        if changed:
            guardian.save(update_fields=[*changed, "updated_at"])

    link, _ = StudentGuardian.objects.get_or_create(
        student=student,
        guardian=guardian,
        defaults={"relationship": relationship, "is_primary": is_primary},
    )
    if is_primary:
        StudentGuardian.objects.filter(student=student).exclude(pk=link.pk).update(is_primary=False)
        if not link.is_primary:
            link.is_primary = True
            link.save(update_fields=["is_primary", "updated_at"])
    return link


# ---------------------------------------------------------------------------
# Admissions
# ---------------------------------------------------------------------------
@tenant_atomic()
def submit_application(**fields) -> Application:
    return Application.objects.create(
        reference=fields.pop("reference", "") or generate_reference(),
        status=ApplicationStatus.SUBMITTED,
        **fields,
    )


@tenant_atomic()
def transition_application(
    application: Application, to_status: str, *, staff=None, notes: str = "", score=None
) -> Application:
    allowed = ALLOWED_TRANSITIONS.get(application.status, set())
    if to_status not in allowed:
        raise InvalidTransition(
            f"An application that is {application.get_status_display().lower()} "
            f"cannot move to {to_status.lower()}.",
            details={"from": application.status, "to": to_status, "allowed": sorted(allowed)},
        )

    now = timezone.now()
    application.status = to_status
    application.decided_by = staff or application.decided_by
    application.decided_at = now
    if notes:
        application.screening_notes = notes
    if score is not None:
        application.screening_score = score
    if to_status == ApplicationStatus.OFFERED:
        application.offered_at = now
    if to_status == ApplicationStatus.ACCEPTED:
        application.accepted_at = now
    application.save()
    return application


@tenant_atomic()
def convert_application_to_student(
    application: Application, *, session=None, class_arm=None, tenant=None
) -> Student:
    """Turn an accepted offer into a student record and an enrolment.

    Idempotent: an application already converted returns its existing student
    rather than creating a duplicate.
    """
    if application.student_id:
        return application.student
    if application.status != ApplicationStatus.ACCEPTED:
        raise InvalidTransition(
            "Only an accepted application can be enrolled.",
            details={"status": application.status},
        )

    student = create_student(
        tenant=tenant,
        first_name=application.first_name,
        last_name=application.last_name,
        other_name=application.other_name,
        gender=application.gender,
        date_of_birth=application.date_of_birth,
        phone=application.phone,
        email=application.email,
        address=application.address,
        state_of_origin=application.state_of_origin,
        lga=application.lga,
        status=StudentStatus.ACTIVE,
        date_admitted=timezone.now().date(),
        current_level=application.level_applied,
        programme=application.programme_applied,
    )

    if application.guardian_phone:
        names = application.guardian_name.split(" ", 1)
        link_guardian(
            student=student,
            phone=application.guardian_phone,
            is_primary=True,
            first_name=names[0] if names else "",
            last_name=names[1] if len(names) > 1 else "",
        )

    if session is not None:
        from apps.academics.services import enrol_student

        enrol_student(
            student=student,
            session=session,
            level=application.level_applied,
            class_arm=class_arm,
            programme=application.programme_applied,
        )

    application.student = student
    application.status = ApplicationStatus.ENROLLED
    application.save(update_fields=["student", "status", "updated_at"])
    return student
