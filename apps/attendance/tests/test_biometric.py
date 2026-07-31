"""Biometric webhook: signature verification and idempotent ingestion."""

import hashlib
import hmac
import json

import pytest
from django.utils import timezone

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.attendance.models import BiometricDevice, BiometricEvent, StudentAttendance
from apps.attendance.services import ingest_biometric_event, verify_device_signature
from apps.people.models import Staff, Student
from apps.tenants.db import schema_context

SECRET = "device-shared-secret"


# --------------------------------------------------------------------------
# Signature check needs no database.
# --------------------------------------------------------------------------
def test_a_correct_signature_verifies():
    device = BiometricDevice(code="GATE1", secret=SECRET)
    body = b'{"external_id":"KC/25/0001"}'
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_device_signature(device, body, signature)


def test_a_tampered_body_fails():
    device = BiometricDevice(code="GATE1", secret=SECRET)
    body = b'{"external_id":"KC/25/0001"}'
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert not verify_device_signature(device, b'{"external_id":"KC/25/0002"}', signature)


def test_the_wrong_key_fails():
    device = BiometricDevice(code="GATE1", secret=SECRET)
    body = b"{}"
    assert not verify_device_signature(
        device, body, hmac.new(b"other-secret", body, hashlib.sha256).hexdigest()
    )


def test_a_missing_signature_fails():
    device = BiometricDevice(code="GATE1", secret=SECRET)
    assert not verify_device_signature(device, b"{}", "")
    assert not verify_device_signature(device, b"{}", None)


# --------------------------------------------------------------------------
# Ingestion
# --------------------------------------------------------------------------
@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def gate(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        Term.objects.create(
            session=session,
            index=1,
            name="First Term",
            start_date="2025-09-01",
            end_date="2025-12-15",
            is_current=True,
        )
        level = ClassLevel.objects.get(code="JSS1")
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="A", current_level=level
        )
        staff = Staff.objects.create(
            staff_number="STF/25/0001", first_name="Amaka", last_name="Obi"
        )
        device = BiometricDevice.objects.create(code="GATE1", secret=SECRET)
        yield {"device": device, "student": student, "staff": staff}


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_student_punch_becomes_attendance(school, gate):
    with schema_context(school.schema_name):
        ingest_biometric_event(
            device=gate["device"], external_id="KC/25/0001", occurred_at=timezone.now()
        )
        record = StudentAttendance.objects.get()
        assert record.student == gate["student"]
        assert record.method == "BIOMETRIC"


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_retried_punch_does_not_double_count(school, gate):
    """Gate terminals retry hard on a flaky school LAN."""
    with schema_context(school.schema_name):
        when = timezone.now()
        ingest_biometric_event(device=gate["device"], external_id="KC/25/0001", occurred_at=when)
        ingest_biometric_event(device=gate["device"], external_id="KC/25/0001", occurred_at=when)
        assert BiometricEvent.objects.count() == 1
        assert StudentAttendance.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_staff_punch_records_check_in_then_check_out(school, gate):
    from apps.attendance.models import StaffAttendance

    with schema_context(school.schema_name):
        # Pinned to the start of the working day: `now() + 8h` crosses midnight
        # whenever the suite runs after 16:00, which files the check-out as a
        # second day's attendance and is nothing to do with the code under test.
        morning = timezone.localtime(timezone.now()).replace(
            hour=7, minute=30, second=0, microsecond=0
        )
        ingest_biometric_event(
            device=gate["device"], external_id="STF/25/0001", occurred_at=morning
        )
        ingest_biometric_event(
            device=gate["device"],
            external_id="STF/25/0001",
            occurred_at=morning + timezone.timedelta(hours=8),
            direction="OUT",
        )
        record = StaffAttendance.objects.get()
        assert record.check_in is not None
        assert record.check_out is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_an_unknown_thumb_is_kept_as_an_unmatched_event(school, gate):
    """An unenrolled finger at the gate is exactly what an admin needs to see."""
    with schema_context(school.schema_name):
        event = ingest_biometric_event(
            device=gate["device"], external_id="WHO-IS-THIS", occurred_at=timezone.now()
        )
        assert event.processed_at is None
        assert event.match_error == "no matching person"
        assert StudentAttendance.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_the_gate_does_not_overrule_the_form_teacher(school, gate):
    """A pupil marked LATE by someone who saw them stays LATE.

    The turnstile knows strictly less than the teacher, and it used to win: a
    plain `update_or_create` reset every hand-marked register entry to PRESENT.
    """
    from apps.academics.selectors import current_term

    with schema_context(school.schema_name):
        record = StudentAttendance.objects.create(
            student=gate["student"],
            term=current_term(),
            date=timezone.localdate(),
            status="LATE",
            method="MANUAL",
            note="Arrived at 08:40.",
        )
        ingest_biometric_event(
            device=gate["device"], external_id="KC/25/0001", occurred_at=timezone.now()
        )
        record.refresh_from_db()
        assert record.status == "LATE"
        assert record.method == "MANUAL"
        assert record.note == "Arrived at 08:40."


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_gate_correction_bumps_the_version(school, gate):
    """`version` is the offline client's optimistic lock. A server-side change
    it does not see is exactly the conflict that scheme exists to report — and
    the biometric path used to move the row without touching it."""
    with schema_context(school.schema_name):
        ingest_biometric_event(
            device=gate["device"], external_id="KC/25/0001", occurred_at=timezone.now()
        )
        record = StudentAttendance.objects.get()
        assert record.version == 1

        ingest_biometric_event(
            device=gate["device"],
            external_id="KC/25/0001",
            occurred_at=timezone.now() + timezone.timedelta(minutes=5),
        )
        record.refresh_from_db()
        assert record.version == 2


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_departed_pupil_is_not_resurrected_by_their_old_thumb(school, gate):
    """Soft-deleting a student removes them from every register; the terminal
    keeps their finger enrolled until somebody gets round to it."""
    with schema_context(school.schema_name):
        gate["student"].soft_delete()
        event = ingest_biometric_event(
            device=gate["device"], external_id="KC/25/0001", occurred_at=timezone.now()
        )
        assert event.match_error == "no matching person"
        assert StudentAttendance.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_a_hand_set_staff_status_survives_a_punch(school, gate):
    """Someone on leave who comes in to collect something is still on leave —
    but the door should still record when they came and went."""
    from apps.attendance.models import StaffAttendance

    with schema_context(school.schema_name):
        StaffAttendance.objects.create(
            staff=gate["staff"], date=timezone.localdate(), status="ON_LEAVE", method="MANUAL"
        )
        morning = timezone.localtime(timezone.now()).replace(hour=7, minute=30, second=0)
        ingest_biometric_event(
            device=gate["device"], external_id="STF/25/0001", occurred_at=morning
        )
        record = StaffAttendance.objects.get()
        assert record.status == "ON_LEAVE"
        assert record.check_in is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_the_webhook_rejects_a_bad_signature(client, school, gate):
    body = json.dumps({"external_id": "KC/25/0001", "occurred_at": timezone.now().isoformat()})
    response = client.post(
        "/api/v1/attendance/biometric/GATE1/",
        body,
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_X_DEVICE_SIGNATURE="deadbeef",
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "DEVICE_UNAUTHORISED"


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_the_webhook_accepts_a_signed_punch(client, school, gate):
    body = json.dumps({"external_id": "KC/25/0001", "occurred_at": timezone.now().isoformat()})
    signature = hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()

    response = client.post(
        "/api/v1/attendance/biometric/GATE1/",
        body,
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_X_DEVICE_SIGNATURE=signature,
    )
    assert response.status_code == 202
    assert response.json()["matched"] is True


@pytest.mark.django_db(transaction=True)
@pytest.mark.db_required
def test_an_unknown_device_gets_the_same_answer_as_a_bad_signature(client, school, gate):
    response = client.post(
        "/api/v1/attendance/biometric/NOPE/",
        "{}",
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_X_DEVICE_SIGNATURE="whatever",
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "DEVICE_UNAUTHORISED"
