"""Academic structure for secondary schools and tertiary institutions.

One set of tables serves both. Where the two genuinely differ the extra
columns are nullable and validated by institution type, rather than forking
into parallel models — a fork would double every downstream join, serializer
and report for the sake of vocabulary that `labels_for()` already handles.

The mapping:

| Concept        | Secondary        | Tertiary                    |
|----------------|------------------|-----------------------------|
| `AcademicSession` | 2025/2026     | 2025/2026                   |
| `Term`         | 3 terms          | 2 semesters                 |
| `ClassLevel`   | JSS1 … SSS3      | 100 … 500 Level             |
| `ClassArm`     | SSS2 Gold        | (unused)                    |
| `Programme`    | (unused)         | ND Computer Science         |
| `Subject`      | Mathematics      | CSC201 Data Structures      |
"""

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel


def _window_is_open(opens_at, closes_at) -> bool:
    """A null bound means "no limit at that end"."""
    now = timezone.now()
    return not (opens_at and now < opens_at) and not (closes_at and now > closes_at)


class AcademicSession(TimeStampedModel):
    name = models.CharField(max_length=20, unique=True, help_text="e.g. 2025/2026")
    start_date = models.DateField()
    end_date = models.DateField()
    #: Exactly one session is current; setting a new one clears the rest.
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        if self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The session must end after it starts."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            AcademicSession.objects.exclude(pk=self.pk).filter(is_current=True).update(
                is_current=False
            )
            # The current term has to sit inside the current session, or the two
            # halves of "now" disagree: `current_term()` keys score entry,
            # registration and the timetable, while `session__is_current` keys
            # enrolment, class lists and invoicing. Rolling the session over and
            # forgetting the term sent this year's marks into last year's term.
            # Demoting is the safe half — `services.set_current_session` picks
            # the replacement, because a wrong term is worse than none.
            Term.objects.filter(is_current=True).exclude(session=self).update(is_current=False)


class Term(TimeStampedModel):
    """Term for a secondary school, semester for a tertiary institution."""

    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="terms")
    #: 1, 2, 3 for terms; 1, 2 for semesters.
    index = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(4)]
    )
    name = models.CharField(max_length=40, help_text="e.g. First Term, First Semester")
    start_date = models.DateField()
    end_date = models.DateField()
    #: Course registration add/drop window. Null at either end means open.
    registration_opens_at = models.DateTimeField(null=True, blank=True)
    registration_closes_at = models.DateTimeField(null=True, blank=True)
    #: Score entry closes outside this window unless an exams officer reopens it.
    result_entry_opens_at = models.DateTimeField(null=True, blank=True)
    result_entry_closes_at = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["session", "index"]
        constraints = [
            models.UniqueConstraint(fields=["session", "index"], name="unique_term_per_session")
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.session.name}"

    def clean(self):
        if self.end_date <= self.start_date:
            raise ValidationError({"end_date": "The term must end after it starts."})

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_current:
            Term.objects.exclude(pk=self.pk).filter(is_current=True).update(is_current=False)
            # The other direction of the invariant in `AcademicSession.save`:
            # marking a term current promotes the session that holds it. The
            # session's own save then demotes every other session and any
            # current term outside this one — this term survives it.
            if not self.session.is_current:
                self.session.is_current = True
                self.session.save(update_fields=["is_current", "updated_at"])

    @property
    def results_published(self) -> bool:
        return self.results_published_at is not None

    @property
    def accepts_registration(self) -> bool:
        return _window_is_open(self.registration_opens_at, self.registration_closes_at)

    @property
    def accepts_scores(self) -> bool:
        return _window_is_open(self.result_entry_opens_at, self.result_entry_closes_at)


class ClassLevel(TimeStampedModel):
    """JSS1 … SSS3, or 100 … 500 Level."""

    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=40)
    #: Sort order and the basis for promotion.
    order = models.PositiveSmallIntegerField(db_index=True)
    #: Where a promoted student goes. Null means this level is terminal.
    next_level = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="previous_levels"
    )
    is_terminal = models.BooleanField(
        default=False, help_text="Final year: graduates, not promotes"
    )

    class Meta:
        ordering = ["order"]

    def __str__(self) -> str:
        return self.name


class Stream(TimeStampedModel):
    """Science / Arts / Commercial. Senior secondary only."""

    code = models.SlugField(max_length=20, unique=True)
    name = models.CharField(max_length=40)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Faculty(TimeStampedModel):
    """Faculty or School. Tertiary only."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "faculties"

    def __str__(self) -> str:
        return self.name


class Department(TimeStampedModel):
    faculty = models.ForeignKey(
        Faculty, null=True, blank=True, on_delete=models.SET_NULL, related_name="departments"
    )
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    head = models.ForeignKey(
        "people.Staff", null=True, blank=True, on_delete=models.SET_NULL, related_name="heads_of"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Award(models.TextChoices):
    ND = "ND", "National Diploma"
    HND = "HND", "Higher National Diploma"
    NCE = "NCE", "Nigeria Certificate in Education"
    BSC = "BSC", "Bachelor's degree"
    PGD = "PGD", "Postgraduate Diploma"
    OTHER = "OTHER", "Other"


class Programme(TimeStampedModel):
    """ND Computer Science, B.Sc Accounting. Tertiary only."""

    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="programmes")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    award = models.CharField(max_length=10, choices=Award.choices, default=Award.ND)
    duration_years = models.PositiveSmallIntegerField(default=2)
    #: Credit-unit bounds enforced at course registration.
    min_credit_units = models.PositiveSmallIntegerField(default=15)
    max_credit_units = models.PositiveSmallIntegerField(default=24)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.award} {self.name}"

    def clean(self):
        if self.max_credit_units < self.min_credit_units:
            raise ValidationError({"max_credit_units": "Maximum cannot be below the minimum."})


class ClassArm(TimeStampedModel):
    """SSS2 Gold, JSS1 A. Secondary only."""

    level = models.ForeignKey(ClassLevel, on_delete=models.CASCADE, related_name="arms")
    name = models.CharField(max_length=30, help_text="A, B, Gold, Silver")
    stream = models.ForeignKey(
        Stream, null=True, blank=True, on_delete=models.SET_NULL, related_name="arms"
    )
    form_teacher = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="form_classes",
    )
    capacity = models.PositiveSmallIntegerField(default=40)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["level__order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["level", "name"], name="unique_arm_per_level")
        ]

    def __str__(self) -> str:
        return f"{self.level.code} {self.name}"


class SubjectCategory(models.TextChoices):
    COMPULSORY = "COMPULSORY", "Compulsory / Core"
    ELECTIVE = "ELECTIVE", "Elective"
    GENERAL = "GENERAL", "General studies"


class Subject(TimeStampedModel):
    """Subject (secondary) or Course (tertiary).

    `credit_units` stays 0 for secondary subjects, where weighting comes from
    the assessment configuration rather than from units.
    """

    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=120)
    category = models.CharField(
        max_length=12, choices=SubjectCategory.choices, default=SubjectCategory.COMPULSORY
    )
    credit_units = models.PositiveSmallIntegerField(default=0)

    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="subjects"
    )
    #: The level this course belongs to. Null for a subject taught across levels.
    level = models.ForeignKey(
        ClassLevel, null=True, blank=True, on_delete=models.SET_NULL, related_name="subjects"
    )
    #: Secondary: restricts an SSS subject to one stream.
    stream = models.ForeignKey(
        Stream, null=True, blank=True, on_delete=models.SET_NULL, related_name="subjects"
    )
    #: 1 or 2 for a course offered in a specific semester; null for either.
    semester_offered = models.PositiveSmallIntegerField(null=True, blank=True)
    prerequisites = models.ManyToManyField(
        "self", blank=True, symmetrical=False, related_name="unlocks"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} {self.title}"


class TeachingAssignment(TimeStampedModel):
    """Who teaches what, to whom, when.

    This table is the object-level permission boundary: a teacher may enter
    scores and mark attendance only for rows that name them here.
    """

    staff = models.ForeignKey(
        "people.Staff", on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assignments")
    session = models.ForeignKey(
        AcademicSession, on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    term = models.ForeignKey(
        Term, null=True, blank=True, on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    #: Secondary: the arm taught. Tertiary: leave null and set `level`.
    class_arm = models.ForeignKey(
        ClassArm, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments"
    )
    level = models.ForeignKey(
        ClassLevel, null=True, blank=True, on_delete=models.CASCADE, related_name="assignments"
    )
    is_lead = models.BooleanField(default=True, help_text="Lead lecturer where a course is shared")

    class Meta:
        ordering = ["subject__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "subject", "session", "term", "class_arm", "level"],
                name="unique_teaching_assignment",
            )
        ]
        indexes = [models.Index(fields=["session", "term", "class_arm"])]

    def __str__(self) -> str:
        target = self.class_arm or self.level
        return f"{self.staff} — {self.subject.code} ({target})"

    def clean(self):
        if self.class_arm is None and self.level is None:
            raise ValidationError("Assign the subject to either a class arm or a level.")


class EnrolmentStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    PROMOTED = "PROMOTED", "Promoted"
    REPEATED = "REPEATED", "Repeating"
    GRADUATED = "GRADUATED", "Graduated"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    TRANSFERRED = "TRANSFERRED", "Transferred out"


class Enrolment(TimeStampedModel):
    """A student's membership of a cohort for one session.

    Secondary: student in SSS2 Gold. Tertiary: student at 200 Level on ND
    Computer Science.
    """

    student = models.ForeignKey(
        "people.Student", on_delete=models.CASCADE, related_name="enrolments"
    )
    session = models.ForeignKey(
        AcademicSession, on_delete=models.CASCADE, related_name="enrolments"
    )
    level = models.ForeignKey(ClassLevel, on_delete=models.PROTECT, related_name="enrolments")
    class_arm = models.ForeignKey(
        ClassArm, null=True, blank=True, on_delete=models.SET_NULL, related_name="enrolments"
    )
    programme = models.ForeignKey(
        Programme, null=True, blank=True, on_delete=models.SET_NULL, related_name="enrolments"
    )
    status = models.CharField(
        max_length=12, choices=EnrolmentStatus.choices, default=EnrolmentStatus.ACTIVE
    )
    roll_number = models.PositiveSmallIntegerField(null=True, blank=True)
    #: Set by the promotion engine in Phase 4; recorded here for the audit trail.
    promotion_note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-session__start_date", "student__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "session"], name="one_enrolment_per_student_per_session"
            )
        ]
        indexes = [models.Index(fields=["session", "class_arm", "status"])]

    def __str__(self) -> str:
        return f"{self.student} — {self.class_arm or self.level} ({self.session})"


class RegistrationStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    ADVISER_APPROVED = "ADVISER_APPROVED", "Approved by adviser"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    DROPPED = "DROPPED", "Dropped"


class SubjectRegistration(TimeStampedModel):
    """A student taking one subject/course in one term.

    Secondary schools use it to record which subjects a pupil offers.
    Tertiary institutions run it through the adviser/HOD approval workflow
    with credit limits, prerequisites and carry-overs.
    """

    enrolment = models.ForeignKey(
        Enrolment, on_delete=models.CASCADE, related_name="subject_registrations"
    )
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT, related_name="registrations")
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="subject_registrations")
    status = models.CharField(
        max_length=20, choices=RegistrationStatus.choices, default=RegistrationStatus.APPROVED
    )
    #: A repeat of a course previously failed. Counts toward credit limits and
    #: is flagged on the result sheet.
    is_carryover = models.BooleanField(default=False)

    adviser_approved_by = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="adviser_approvals",
    )
    adviser_approved_at = models.DateTimeField(null=True, blank=True)
    hod_approved_by = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="hod_approvals",
    )
    hod_approved_at = models.DateTimeField(null=True, blank=True)
    dropped_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["subject__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["enrolment", "subject", "term"], name="unique_subject_per_term"
            )
        ]
        indexes = [models.Index(fields=["term", "status"])]

    def __str__(self) -> str:
        return f"{self.enrolment.student} — {self.subject.code}"

    @property
    def is_active(self) -> bool:
        return self.status not in {RegistrationStatus.DROPPED, RegistrationStatus.REJECTED}


class Weekday(models.IntegerChoices):
    MONDAY = 1, "Monday"
    TUESDAY = 2, "Tuesday"
    WEDNESDAY = 3, "Wednesday"
    THURSDAY = 4, "Thursday"
    FRIDAY = 5, "Friday"
    SATURDAY = 6, "Saturday"


class Room(TimeStampedModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80, blank=True)
    capacity = models.PositiveSmallIntegerField(default=40)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return self.name or self.code


class TimetableEntry(TimeStampedModel):
    """One recurring weekly period.

    Clash detection lives in `services.assert_no_clash` rather than in a
    database constraint: overlap is a range comparison across three different
    resources (teacher, room, class), which no single unique index expresses.
    """

    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="timetable_entries")
    day = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="timetable_entries")
    staff = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="timetable_entries",
    )
    class_arm = models.ForeignKey(
        ClassArm, null=True, blank=True, on_delete=models.CASCADE, related_name="timetable_entries"
    )
    level = models.ForeignKey(
        ClassLevel,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="timetable_entries",
    )
    room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL, related_name="timetable_entries"
    )

    class Meta:
        ordering = ["day", "start_time"]
        indexes = [
            models.Index(fields=["term", "day"]),
            models.Index(fields=["term", "class_arm", "day"]),
        ]
        verbose_name_plural = "timetable entries"

    def __str__(self) -> str:
        return f"{self.get_day_display()} {self.start_time:%H:%M} {self.subject.code}"

    def clean(self):
        if self.end_time <= self.start_time:
            raise ValidationError({"end_time": "A period must end after it starts."})
        if self.class_arm is None and self.level is None:
            raise ValidationError("A period must belong to a class arm or a level.")
