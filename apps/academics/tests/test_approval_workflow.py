"""Adviser first, then HOD — the order the docstring declared and nothing kept.

`approve_registration` checked only the credit minimum. Everything else in its
own first sentence was documentation: an HOD could finalise a DRAFT the
student never submitted, and either step could be applied to a course the
student had already DROPPED — resurrecting it into the credit count and onto
the report card with `dropped_at` still set.

The other half was worse. `RegistrationStatus.REJECTED` and
`SubjectRegistration.rejection_reason` were declared from the start and no
code could write either, while three selectors and
`SubjectRegistration.is_active` all read them. An adviser could approve and
could not refuse, so saying no meant leaving the registration in SUBMITTED —
which `assessment.selectors.COUNTED_STATUSES` scores anyway.
"""

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassLevel,
    Department,
    Faculty,
    Programme,
    RegistrationStatus,
    Subject,
    Term,
)
from apps.academics.services import (
    RegistrationNotPending,
    approve_registration,
    drop_subject,
    enrol_student,
    register_subjects,
    reject_registration,
    reopen_registration,
)
from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
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
            Subject.objects.create(code=f"CSC10{n}", title=f"Course {n}", credit_units=6)
            for n in range(1, 4)
        ]
        adviser = Staff.objects.create(staff_number="STF/1", first_name="Ada", last_name="Nwosu")
        hod = Staff.objects.create(staff_number="STF/2", first_name="Bola", last_name="Ade")
        yield {
            "enrolment": enrolment,
            "term": term,
            "subjects": subjects,
            "adviser": adviser,
            "hod": hod,
        }


def _submitted(setup, submit=True):
    return register_subjects(
        enrolment=setup["enrolment"],
        term=setup["term"],
        subjects=setup["subjects"],
        submit=submit,
    )[0]


# ---------------------------------------------------------------------------
# The declared order
# ---------------------------------------------------------------------------
def test_the_hod_cannot_skip_the_adviser(poly, setup):
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        with pytest.raises(RegistrationNotPending) as caught:
            approve_registration(registration=registration, staff=setup["hod"], as_hod=True)
        assert caught.value.details["expected"] == RegistrationStatus.ADVISER_APPROVED
        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.SUBMITTED


def test_a_draft_the_student_never_submitted_cannot_be_approved(poly, setup):
    """DRAFT is the student's own unsubmitted basket."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup, submit=False)
        assert registration.status == RegistrationStatus.DRAFT
        with pytest.raises(RegistrationNotPending):
            approve_registration(registration=registration, staff=setup["adviser"])


def test_the_declared_order_still_works(poly, setup):
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        approve_registration(registration=registration, staff=setup["adviser"])
        assert registration.status == RegistrationStatus.ADVISER_APPROVED
        approve_registration(registration=registration, staff=setup["hod"], as_hod=True)
        assert registration.status == RegistrationStatus.APPROVED


def test_approving_twice_is_not_an_error(poly, setup):
    """Idempotent: a double-tap on a slow connection is not a fault."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        approve_registration(registration=registration, staff=setup["adviser"])
        approve_registration(registration=registration, staff=setup["hod"], as_hod=True)
        again = approve_registration(registration=registration, staff=setup["hod"], as_hod=True)
        assert again.status == RegistrationStatus.APPROVED


# ---------------------------------------------------------------------------
# Terminal is terminal
# ---------------------------------------------------------------------------
def test_a_dropped_course_cannot_be_approved_back_to_life(poly, setup):
    """It would count toward credits and be scored, with `dropped_at` set."""
    from apps.academics.services import registered_credit_units

    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        drop_subject(registration=registration)
        before = registered_credit_units(setup["enrolment"], setup["term"])

        with pytest.raises(RegistrationNotPending):
            approve_registration(registration=registration, staff=setup["adviser"])

        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.DROPPED
        assert registered_credit_units(setup["enrolment"], setup["term"]) == before


def test_a_rejected_course_cannot_be_approved_either(poly, setup):
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        reject_registration(
            registration=registration, staff=setup["adviser"], reason="Prerequisite outstanding."
        )
        with pytest.raises(RegistrationNotPending):
            approve_registration(registration=registration, staff=setup["hod"], as_hod=True)


# ---------------------------------------------------------------------------
# Refusal, which had no write path at all
# ---------------------------------------------------------------------------
def test_rejecting_writes_the_state_everything_already_read(poly, setup):
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        reject_registration(
            registration=registration, staff=setup["adviser"], reason="Clashes with CSC201."
        )
        registration.refresh_from_db()

        assert registration.status == RegistrationStatus.REJECTED
        assert registration.rejection_reason == "Clashes with CSC201."
        assert registration.adviser_approved_by_id == setup["adviser"].pk
        assert not registration.is_active


def test_a_rejected_course_stops_counting_and_stops_being_scored(poly, setup):
    """Before this existed, refusing meant leaving it in SUBMITTED — which
    `COUNTED_STATUSES` puts on the report card regardless."""
    from apps.academics.services import registered_credit_units
    from apps.assessment.selectors import counted_registrations

    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        assert registered_credit_units(setup["enrolment"], setup["term"]) == 18
        assert counted_registrations(setup["term"]).filter(pk=registration.pk).exists()

        reject_registration(
            registration=registration, staff=setup["adviser"], reason="Not offered this semester."
        )
        assert registered_credit_units(setup["enrolment"], setup["term"]) == 12
        assert not counted_registrations(setup["term"]).filter(pk=registration.pk).exists()


def test_rejecting_a_dropped_course_leaves_it_dropped(poly, setup):
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        drop_subject(registration=registration)
        reject_registration(registration=registration, staff=setup["adviser"], reason="Too late.")
        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.DROPPED


def test_a_rejection_needs_a_reason(client, poly, setup):
    """ "Rejected" with no explanation is a support call, not a decision."""
    from apps.accounts.models import Role, User
    from apps.auth_phone import tokens

    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        user = User.objects.create_user("+2348031111111", first_name="Adviser")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=poly)

    headers = {
        "HTTP_X_TENANT_SLUG": poly.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }
    url = f"/api/v1/academics/registrations/{registration.pk}/reject/"

    assert client.post(url, {}, content_type="application/json", **headers).status_code == 400

    ok = client.post(
        url, {"reason": "Prerequisite outstanding."}, content_type="application/json", **headers
    )
    assert ok.status_code == 200, ok.content
    with schema_context(poly.schema_name):
        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.REJECTED


# ---------------------------------------------------------------------------
# The way back from a refusal
# ---------------------------------------------------------------------------
def test_re_registering_a_refused_course_used_to_be_impossible(poly, setup):
    """`approve_registration` says "register it again rather than approving
    it", and `check_registration` answered "Already registered." to exactly
    that attempt — the documented route did not exist."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        reject_registration(
            registration=registration, staff=setup["adviser"], reason="Prerequisite outstanding."
        )

        again = register_subjects(
            enrolment=setup["enrolment"],
            term=setup["term"],
            subjects=[registration.subject],
        )

        assert len(again) == 1
        assert again[0].pk == registration.pk  # unique_subject_per_term: one row, revived
        assert again[0].status == RegistrationStatus.SUBMITTED
        assert again[0].rejection_reason == ""
        assert again[0].adviser_approved_by_id is None


def test_re_registering_a_dropped_course_no_longer_crashes(poly, setup):
    """`check_registration` waved it through and `bulk_create` hit
    `unique_subject_per_term` — a 500 on the add/drop screen's own retry."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        drop_subject(registration=registration)

        again = register_subjects(
            enrolment=setup["enrolment"], term=setup["term"], subjects=[registration.subject]
        )

        assert again[0].pk == registration.pk
        assert again[0].status == RegistrationStatus.SUBMITTED
        assert again[0].dropped_at is None
        # Dropping in week two and taking it back in week three is not a repeat.
        assert again[0].is_carryover is False


def test_a_refusal_can_be_put_back_in_the_queue(poly, setup):
    """The one decision here nobody could take back: re-registering belongs to
    the student and only works while the add/drop window is open."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        reject_registration(registration=registration, staff=setup["adviser"], reason="Wrong term.")

        reopen_registration(registration=registration)
        registration.refresh_from_db()

        assert registration.status == RegistrationStatus.SUBMITTED
        assert registration.rejection_reason == ""
        assert registration.adviser_approved_by_id is None
        assert registration.adviser_approved_at is None
        assert registration.is_active
        # And it can be signed off from there, which is the whole point.
        approve_registration(registration=registration, staff=setup["adviser"])
        assert registration.status == RegistrationStatus.ADVISER_APPROVED


def test_reopening_names_who_undid_the_refusal(poly, setup):
    """Nothing on the registration itself says who reopened it — the two
    `*_approved_by` columns are cleared on purpose — so the audit row is the
    only place a registrar can look, and it has to carry the erased reason."""
    with schema_context(poly.schema_name):
        user = User.objects.create_user("+2348030000001", first_name="Ada", last_name="Nwosu")
        registration = _submitted(setup)
        reject_registration(registration=registration, staff=setup["adviser"], reason="Wrong term.")

        reopen_registration(registration=registration, actor=user)

        entry = AuditLog.objects.filter(action=AuditAction.REGISTRATION_REOPENED).get()
        assert entry.actor_id == user.pk
        assert "Ada Nwosu" in entry.actor_label
        assert entry.object_id == str(registration.pk)
        assert entry.before["rejection_reason"] == "Wrong term."


def test_refusing_and_dropping_leave_their_own_trail(poly, setup):
    """A reopen clears `adviser_approved_by`, so the refusal's audit row is the
    only lasting record of who refused; a drop writes to no `*_by` column at
    all, so it has nothing else."""
    with schema_context(poly.schema_name):
        user = User.objects.create_user("+2348030000002", first_name="Ada", last_name="Nwosu")
        setup["adviser"].user = user
        setup["adviser"].save(update_fields=["user"])

        registrations = register_subjects(
            enrolment=setup["enrolment"],
            term=setup["term"],
            subjects=setup["subjects"],
            submit=True,
        )
        refused, dropped = registrations[0], registrations[1]

        reject_registration(registration=refused, staff=setup["adviser"], reason="Wrong term.")
        entry = AuditLog.objects.filter(action=AuditAction.REGISTRATION_REJECTED).get()
        assert entry.actor_id == user.pk
        assert entry.object_id == str(refused.pk)
        assert entry.after["rejection_reason"] == "Wrong term."
        assert entry.before["status"] == RegistrationStatus.SUBMITTED

        drop_subject(registration=dropped, actor=user)
        entry = AuditLog.objects.filter(action=AuditAction.SUBJECT_DROPPED).get()
        assert entry.actor_id == user.pk
        assert "Ada Nwosu" in entry.actor_label
        assert entry.object_id == str(dropped.pk)


def test_only_a_refusal_can_be_put_back(poly, setup):
    """Not an undo for approving, and not a resurrection for a dropped course
    — `drop_subject` has its own rules about the add/drop window."""
    with schema_context(poly.schema_name):
        registration = _submitted(setup)
        with pytest.raises(RegistrationNotPending):
            reopen_registration(registration=registration)

        drop_subject(registration=registration)
        with pytest.raises(RegistrationNotPending):
            reopen_registration(registration=registration)
        registration.refresh_from_db()
        assert registration.status == RegistrationStatus.DROPPED
