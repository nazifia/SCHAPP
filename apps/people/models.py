"""Students, guardians, staff and admissions.

A `Student` is not a `User`. Most Nigerian secondary pupils have no phone and
never sign in; their guardian does. The optional `user` link exists for those
who do log in — senior secondary pupils, and every tertiary student.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models

from apps.common.fields import EncryptedTextField, mask_tail
from apps.common.models import SoftDeleteModel, TimeStampedModel
from apps.numbering.fields import PhoneNumberField


class Gender(models.TextChoices):
    MALE = "M", "Male"
    FEMALE = "F", "Female"


class StudentStatus(models.TextChoices):
    APPLICANT = "APPLICANT", "Applicant"
    ADMITTED = "ADMITTED", "Admitted, not yet enrolled"
    ACTIVE = "ACTIVE", "Active"
    GRADUATED = "GRADUATED", "Graduated"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    SUSPENDED = "SUSPENDED", "Suspended"
    TRANSFERRED = "TRANSFERRED", "Transferred out"


class PersonMixin(models.Model):
    """Fields every person in a Nigerian school record carries."""

    first_name = models.CharField(max_length=60)
    last_name = models.CharField(max_length=60)
    other_name = models.CharField(max_length=60, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone = PhoneNumberField(blank=True, default="")
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    state_of_origin = models.CharField(max_length=40, blank=True)
    lga = models.CharField(max_length=60, blank=True, verbose_name="LGA")
    photo = models.ImageField(upload_to="people/", null=True, blank=True)
    #: Encrypted, masked in detail views, never in lists.
    nin = EncryptedTextField(blank=True, default="")

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return self.full_name

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.other_name, self.last_name) if p)

    @property
    def masked_nin(self) -> str:
        return mask_tail(self.nin)


class Student(PersonMixin, SoftDeleteModel):
    """Soft-deleted, never hard-deleted: a school must be able to produce a
    transcript for someone who left in 2019."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="student_profile",
    )
    admission_number = models.CharField(max_length=40, unique=True)
    #: Tertiary only, and only once matriculation happens.
    matriculation_number = models.CharField(max_length=40, blank=True, db_index=True)

    status = models.CharField(
        max_length=12, choices=StudentStatus.choices, default=StudentStatus.ACTIVE, db_index=True
    )
    date_admitted = models.DateField(null=True, blank=True)
    date_left = models.DateField(null=True, blank=True)

    #: Denormalised current position, kept in step by the enrolment service so
    #: class lists and dashboards do not join through every past session.
    current_level = models.ForeignKey(
        "academics.ClassLevel",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_students",
    )
    current_arm = models.ForeignKey(
        "academics.ClassArm",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="current_students",
    )
    programme = models.ForeignKey(
        "academics.Programme",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )
    stream = models.ForeignKey(
        "academics.Stream",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="students",
    )

    guardians = models.ManyToManyField(
        "Guardian", through="StudentGuardian", related_name="students"
    )

    blood_group = models.CharField(max_length=5, blank=True)
    medical_notes = models.TextField(blank=True)
    religion = models.CharField(max_length=30, blank=True)

    class Meta(SoftDeleteModel.Meta):
        ordering = ["last_name", "first_name"]
        indexes = [
            models.Index(fields=["status", "current_arm"]),
            models.Index(fields=["status", "programme"]),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.admission_number})"

    @property
    def display_number(self) -> str:
        return self.matriculation_number or self.admission_number


class Relationship(models.TextChoices):
    FATHER = "FATHER", "Father"
    MOTHER = "MOTHER", "Mother"
    GUARDIAN = "GUARDIAN", "Guardian"
    SIBLING = "SIBLING", "Sibling"
    OTHER = "OTHER", "Other"


class Guardian(PersonMixin, TimeStampedModel):
    """One account, many wards.

    Guardians are matched on phone number, so a parent with three children in
    the school is one row and one login, not three.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="guardian_profile",
    )
    #: Unique, because matching on phone is the whole point — but a guardian
    #: recorded from a paper form may have no number yet. "No number" has to be
    #: NULL rather than "": both MySQL and SQLite skip NULLs in a unique index,
    #: whereas the partial index that would exempt "" does not exist in MySQL —
    #: Django drops such a constraint silently there, so uniqueness would go
    #: unenforced in production while passing every test on SQLite.
    phone = PhoneNumberField(unique=True, null=True, blank=True, default=None)

    occupation = models.CharField(max_length=80, blank=True)
    employer = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def save(self, *args, **kwargs):
        if not self.phone:
            self.phone = None
        super().save(*args, **kwargs)


class StudentGuardian(TimeStampedModel):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardian_links")
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE, related_name="ward_links")
    relationship = models.CharField(
        max_length=10, choices=Relationship.choices, default=Relationship.GUARDIAN
    )
    #: Receives the fee reminders and the report card.
    is_primary = models.BooleanField(default=False)
    can_pick_up = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_primary"]
        constraints = [
            models.UniqueConstraint(fields=["student", "guardian"], name="unique_student_guardian")
        ]

    def __str__(self) -> str:
        return f"{self.guardian} — {self.get_relationship_display()} of {self.student}"


class StaffType(models.TextChoices):
    TEACHING = "TEACHING", "Teaching"
    NON_TEACHING = "NON_TEACHING", "Non-teaching"


class StaffStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    ON_LEAVE = "ON_LEAVE", "On leave"
    SUSPENDED = "SUSPENDED", "Suspended"
    EXITED = "EXITED", "Exited"


class Staff(PersonMixin, SoftDeleteModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff_profile",
    )
    staff_number = models.CharField(max_length=40, unique=True)
    staff_type = models.CharField(
        max_length=14, choices=StaffType.choices, default=StaffType.TEACHING
    )
    designation = models.CharField(max_length=80, blank=True)
    department = models.ForeignKey(
        "academics.Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="staff",
    )
    qualification = models.CharField(max_length=120, blank=True)
    date_employed = models.DateField(null=True, blank=True)
    date_exited = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=10, choices=StaffStatus.choices, default=StaffStatus.ACTIVE, db_index=True
    )
    #: Payroll details land in Phase 5; the account number lives here because
    #: HR captures it at onboarding.
    bank_name = models.CharField(max_length=60, blank=True)
    bank_account_number = EncryptedTextField(blank=True, default="")

    class Meta(SoftDeleteModel.Meta):
        ordering = ["last_name", "first_name"]
        verbose_name_plural = "staff"

    def __str__(self) -> str:
        return f"{self.full_name} ({self.staff_number})"


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "SUBMITTED", "Submitted"
    SCREENING = "SCREENING", "Under screening"
    OFFERED = "OFFERED", "Offer made"
    ACCEPTED = "ACCEPTED", "Offer accepted"
    ENROLLED = "ENROLLED", "Enrolled"
    REJECTED = "REJECTED", "Rejected"
    WITHDRAWN = "WITHDRAWN", "Withdrawn by applicant"


class Application(PersonMixin, TimeStampedModel):
    """An admission application, before the applicant becomes a Student.

    Kept separate so a rejected applicant never pollutes class lists, fee
    runs or headcounts.
    """

    reference = models.CharField(max_length=30, unique=True)
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT, related_name="applications"
    )
    level_applied = models.ForeignKey(
        "academics.ClassLevel", on_delete=models.PROTECT, related_name="applications"
    )
    programme_applied = models.ForeignKey(
        "academics.Programme",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="applications",
    )
    previous_school = models.CharField(max_length=150, blank=True)
    guardian_name = models.CharField(max_length=120, blank=True)
    guardian_phone = PhoneNumberField(blank=True, default="")

    status = models.CharField(
        max_length=12,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED,
        db_index=True,
    )
    screening_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    screening_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL, related_name="admission_decisions"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    offered_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    #: Acceptance-fee settlement is wired to invoicing in Phase 5.
    acceptance_fee_paid = models.BooleanField(default=False)
    #: Set once the offer is converted, so conversion is idempotent.
    student = models.OneToOneField(
        Student, null=True, blank=True, on_delete=models.SET_NULL, related_name="application"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["session", "status"])]

    def __str__(self) -> str:
        return f"{self.reference} — {self.full_name}"

    def clean(self):
        if self.status == ApplicationStatus.ENROLLED and self.student is None:
            raise ValidationError("An enrolled application must point at a student record.")


#: What a school actually uploads against a pupil, and nothing else.
#:
#: The whitelist is the security control, not a convenience: these files are
#: served back from the application's own origin, so an `.html` or an `.svg`
#: among them is stored XSS against every session on that host. Extensions
#: rather than sniffed content types because the extension is what the server
#: picks a `Content-Type` from — it is the thing that decides whether a browser
#: renders the file or downloads it.
DOCUMENT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "webp", "heic"]

#: 10 MB. A phone photo of a birth certificate is two; anything ten times that
#: is a mistake or an attempt to fill the disk.
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024


def validate_document_size(uploaded) -> None:
    if uploaded.size > MAX_DOCUMENT_BYTES:
        raise ValidationError(
            f"This file is {uploaded.size / 1_048_576:.1f} MB; the limit is "
            f"{MAX_DOCUMENT_BYTES // 1_048_576} MB."
        )


class StudentDocument(TimeStampedModel):
    """Birth certificate, testimonial, O-level result, passport photo."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=120)
    file = models.FileField(
        upload_to="student-documents/",
        validators=[FileExtensionValidator(DOCUMENT_EXTENSIONS), validate_document_size],
    )
    uploaded_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploads"
    )
    #: PLACEHOLDER: no scanner. This said "Phase 9 wires the scanner" and Phase
    #: 9 shipped without one, which made the field a claim that uploads were
    #: checked. They are not — `DOCUMENT_EXTENSIONS` and the size cap are the
    #: whole of the protection, and they stop a served-back XSS payload, not
    #: malware inside a genuine PDF. Anything that sets this must actually scan.
    virus_scanned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} — {self.student}"
