"""Attendance for students and staff.

One row per student per day per subject. `subject` is null for the daily
register a form teacher takes; set for a per-period record. That distinction
is in the unique constraint, so a school can run either or both without the
two colliding.
"""

from django.db import models

from apps.common.models import TimeStampedModel


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    ABSENT = "ABSENT", "Absent"
    LATE = "LATE", "Late"
    EXCUSED = "EXCUSED", "Excused"


class MarkingMethod(models.TextChoices):
    MANUAL = "MANUAL", "Marked by hand"
    QR = "QR", "QR scan"
    BIOMETRIC = "BIOMETRIC", "Biometric device"
    IMPORT = "IMPORT", "Bulk import"


class StudentAttendance(TimeStampedModel):
    student = models.ForeignKey(
        "people.Student", on_delete=models.CASCADE, related_name="attendance"
    )
    term = models.ForeignKey(
        "academics.Term", on_delete=models.CASCADE, related_name="student_attendance"
    )
    date = models.DateField(db_index=True)
    #: Null for the daily register; set for a single period.
    subject = models.ForeignKey(
        "academics.Subject",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="attendance",
    )
    class_arm = models.ForeignKey(
        "academics.ClassArm",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance",
    )
    status = models.CharField(
        max_length=8, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT
    )
    method = models.CharField(
        max_length=10, choices=MarkingMethod.choices, default=MarkingMethod.MANUAL
    )
    marked_by = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_marked",
    )
    marked_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)

    #: Incremented on every server-side edit. The offline client sends the
    #: version it last saw; a mismatch is a conflict, not a silent overwrite.
    version = models.PositiveIntegerField(default=1)
    #: When the teacher actually marked it on the device, which may be hours
    #: before the phone found a signal. Reports use this, not `created_at`.
    recorded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "student__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "date", "subject"],
                name="unique_student_attendance_per_period",
            )
        ]
        indexes = [
            models.Index(fields=["term", "date", "class_arm"]),
            models.Index(fields=["student", "term"]),
        ]

    def __str__(self) -> str:
        return f"{self.student} {self.date} {self.status}"


class StaffAttendance(TimeStampedModel):
    staff = models.ForeignKey("people.Staff", on_delete=models.CASCADE, related_name="attendance")
    date = models.DateField(db_index=True)
    check_in = models.DateTimeField(null=True, blank=True)
    check_out = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=8, choices=AttendanceStatus.choices, default=AttendanceStatus.PRESENT
    )
    method = models.CharField(
        max_length=10, choices=MarkingMethod.choices, default=MarkingMethod.MANUAL
    )
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "date"], name="unique_staff_attendance_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.staff} {self.date} {self.status}"


class BiometricDevice(TimeStampedModel):
    """A fingerprint or face terminal at the gate.

    Devices post to a webhook rather than us polling them, so each carries a
    shared secret used to sign its payloads.
    """

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80, blank=True)
    location = models.CharField(max_length=80, blank=True)
    secret = models.CharField(max_length=64)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name or self.code


class BiometricEvent(TimeStampedModel):
    """Raw device punch, kept whether or not it could be matched.

    Storing unmatched events is the point: an unenrolled thumb at the gate is
    exactly what an administrator needs to see.
    """

    device = models.ForeignKey(BiometricDevice, on_delete=models.CASCADE, related_name="events")
    #: The identifier the device knows the person by (enrolment id on the terminal).
    external_id = models.CharField(max_length=64, db_index=True)
    occurred_at = models.DateTimeField()
    direction = models.CharField(max_length=4, choices=[("IN", "In"), ("OUT", "Out")], default="IN")
    payload = models.JSONField(default=dict, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    match_error = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        constraints = [
            # Devices retry aggressively on a flaky link; the same punch must
            # not become two attendance records.
            models.UniqueConstraint(
                fields=["device", "external_id", "occurred_at"], name="unique_biometric_punch"
            )
        ]

    def __str__(self) -> str:
        return f"{self.device} {self.external_id} {self.occurred_at:%Y-%m-%d %H:%M}"
