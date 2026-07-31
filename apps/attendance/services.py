"""Attendance writes, built around the offline case.

The teacher marks forty pupils in a staffroom with no signal, and the phone
syncs an hour later. That means: one request for the whole class, one
transaction, per-row errors, and a version check so a late sync cannot
silently overwrite a correction someone made in the meantime.
"""

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import date as date_type

from django.utils import timezone

from apps.api.bulk import BulkOutcome, RowError, atomic_bulk
from apps.api.exceptions import AppError
from apps.tenants.db import tenant_atomic

from .models import (
    AttendanceStatus,
    BiometricDevice,
    BiometricEvent,
    MarkingMethod,
    StaffAttendance,
    StudentAttendance,
)

logger = logging.getLogger(__name__)


class AttendanceError(AppError):
    default_code = "ATTENDANCE_ERROR"


class NotYourClass(AttendanceError):
    default_code = "NOT_YOUR_CLASS"
    default_detail = "You are not assigned to this class."


@dataclass
class AttendanceRow:
    student_id: str
    status: str
    note: str = ""
    #: Version the client last saw. None means "I am creating this".
    version: int | None = None
    #: When the device recorded it, which may predate the sync by hours.
    recorded_at: object = None


def mark_bulk(
    *,
    term,
    date: date_type,
    rows: list[AttendanceRow],
    subject=None,
    class_arm=None,
    marked_by=None,
    method: str = MarkingMethod.MANUAL,
    allowed_student_ids: set | None = None,
) -> BulkOutcome:
    """Apply a whole register in one transaction.

    `allowed_student_ids` is the caller's scope: rows outside it are refused
    individually rather than the request being rejected wholesale, so a stale
    class list on the device names the offending pupil.
    """

    def handler(outcome: BulkOutcome) -> None:
        existing = {
            str(record.student_id): record
            for record in StudentAttendance.objects.select_for_update().filter(
                term=term,
                date=date,
                subject=subject,
                student_id__in=[r.student_id for r in rows],
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

            if row.status not in AttendanceStatus.values:
                outcome.errors.append(
                    RowError(index, row.student_id, "BAD_STATUS", f"Unknown status {row.status!r}.")
                )
                continue

            record = existing.get(str(row.student_id))
            if record is None:
                StudentAttendance.objects.create(
                    student_id=row.student_id,
                    term=term,
                    date=date,
                    subject=subject,
                    class_arm=class_arm,
                    status=row.status,
                    note=row.note,
                    method=method,
                    marked_by=marked_by,
                    recorded_at=row.recorded_at or timezone.now(),
                )
                outcome.created += 1
                continue

            # Last-write-wins, but only against the version the client saw.
            if row.version is not None and row.version != record.version:
                outcome.errors.append(
                    RowError(
                        index,
                        row.student_id,
                        "CONFLICT",
                        f"Changed on the server since you last synced "
                        f"(now {record.get_status_display()}).",
                    )
                )
                continue

            record.status = row.status
            record.note = row.note
            record.method = method
            record.marked_by = marked_by
            record.recorded_at = row.recorded_at or record.recorded_at
            record.version += 1
            record.save(
                update_fields=[
                    "status",
                    "note",
                    "method",
                    "marked_by",
                    "recorded_at",
                    "version",
                    "updated_at",
                ]
            )
            outcome.updated += 1

    return atomic_bulk(handler)


@tenant_atomic()
def mark_staff(*, staff, date: date_type, status: str, method=MarkingMethod.MANUAL, **fields):
    record, _ = StaffAttendance.objects.update_or_create(
        staff=staff, date=date, defaults={"status": status, "method": method, **fields}
    )
    return record


# ---------------------------------------------------------------------------
# Biometric webhook
# ---------------------------------------------------------------------------
def verify_device_signature(device: BiometricDevice, body: bytes, signature: str) -> bool:
    """HMAC-SHA256 over the raw body. Constant-time compare.

    A gate terminal sits on the school LAN where anyone can reach it, so an
    unsigned webhook is an open door to fake attendance.
    """
    expected = hmac.new(device.secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").strip())


@tenant_atomic()
def ingest_biometric_event(
    *,
    device: BiometricDevice,
    external_id: str,
    occurred_at,
    direction: str = "IN",
    payload: dict | None = None,
) -> BiometricEvent:
    """Record a punch and turn it into attendance if the person is known.

    Idempotent on (device, external_id, occurred_at): terminals retry.
    """
    event, created = BiometricEvent.objects.get_or_create(
        device=device,
        external_id=external_id,
        occurred_at=occurred_at,
        defaults={"direction": direction, "payload": payload or {}},
    )
    if not created:
        return event

    device.last_seen_at = timezone.now()
    device.save(update_fields=["last_seen_at", "updated_at"])

    _resolve_event(event)
    return event


def _resolve_event(event: BiometricEvent) -> None:
    from apps.academics.selectors import current_term
    from apps.people.models import Staff, Student

    term = current_term()
    day = event.occurred_at.date()

    # `.alive()`, not `.objects`: a pupil who left is soft-deleted and vanishes
    # from every register, but their thumb is still enrolled on the terminal
    # until someone gets round to it. A punch from a deleted record would
    # resurrect them into today's attendance.
    staff = Staff.objects.alive().filter(staff_number=event.external_id).first()
    if staff is not None:
        _apply_staff_punch(staff, day, event)
        event.processed_at = timezone.now()
        event.save(update_fields=["processed_at", "updated_at"])
        return

    student = Student.objects.alive().filter(admission_number=event.external_id).first()
    if student is None:
        student = Student.objects.alive().filter(matriculation_number=event.external_id).first()

    if student is None or term is None:
        event.match_error = "no matching person" if student is None else "no current term"
        event.save(update_fields=["match_error", "updated_at"])
        return

    _apply_student_punch(student, term, day, event)
    event.processed_at = timezone.now()
    event.save(update_fields=["processed_at", "updated_at"])


def _apply_student_punch(student, term, day, event: BiometricEvent) -> None:
    """A gate punch marks a pupil present — unless a human already said otherwise.

    Two things this used to get wrong, both from a plain `update_or_create`:

    * it overwrote the form teacher's mark. A pupil recorded LATE or EXCUSED
      by someone who saw them was reset to PRESENT by the turnstile, and the
      turnstile knows strictly less;
    * it left `version` untouched, so the offline client's optimistic lock —
      the whole reason `mark_bulk` tracks a version at all — never noticed the
      row had moved. The teacher's next sync would overwrite silently instead
      of reporting a conflict.
    """
    record = (
        StudentAttendance.objects.select_for_update()
        .filter(student=student, date=day, subject=None)
        .first()
    )

    if record is None:
        StudentAttendance.objects.create(
            student=student,
            term=term,
            date=day,
            subject=None,
            class_arm=student.current_arm,
            status=AttendanceStatus.PRESENT,
            method=MarkingMethod.BIOMETRIC,
            recorded_at=event.occurred_at,
        )
        return

    if record.method == MarkingMethod.MANUAL:
        return  # a person looked at this pupil; the gate did not

    record.status = AttendanceStatus.PRESENT
    record.method = MarkingMethod.BIOMETRIC
    record.recorded_at = event.occurred_at
    record.version += 1
    record.save(update_fields=["status", "method", "recorded_at", "version", "updated_at"])


def _apply_staff_punch(staff, day, event: BiometricEvent) -> None:
    """Same rule for staff: record the times, leave a hand-set status alone.

    A member of staff marked ON_LEAVE who comes in to collect something should
    not be flipped to PRESENT by the door.
    """
    field = "check_in" if event.direction == "IN" else "check_out"
    existing = StaffAttendance.objects.filter(staff=staff, date=day).first()
    if existing is not None and existing.method == MarkingMethod.MANUAL:
        setattr(existing, field, event.occurred_at)
        existing.save(update_fields=[field, "updated_at"])
        return

    mark_staff(
        staff=staff,
        date=day,
        status=AttendanceStatus.PRESENT,
        method=MarkingMethod.BIOMETRIC,
        **{field: event.occurred_at},
    )
