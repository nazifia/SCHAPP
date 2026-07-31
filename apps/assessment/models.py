"""Scores, grading scales and the results computed from them.

The shape of a Nigerian result is the same everywhere and configurable
nowhere-near-enough by most systems, so three things are data rather than
code:

* **components** — CA1 20, CA2 20, Exam 60 for one school; 30/70 for the next;
  a per-subject override for practicals in the third;
* **grading scales** — WAEC A1…F9 for secondary, a 5-point or 4-point scale
  for tertiary, and a different scale for junior and senior classes if the
  school wants one;
* **the pass mark** — 40 in most schools, 50 in some, and it decides both the
  grade and the promotion.

`SubjectResult` and `TermResult` are derived tables. They exist because a
report card, a broadsheet and a transcript would otherwise re-aggregate every
score in the school on every request; they are rebuilt by
`services.recompute_term`, never edited by hand.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from apps.common.models import TimeStampedModel

from .grading import Band


class AssessmentComponent(TimeStampedModel):
    """One column of the mark sheet: CA1, CA2, Exam, Practical.

    Scope is a two-step narrowing — a component may be defined for the whole
    school, for one level, or for one subject. `selectors.components_for()`
    resolves it. There is deliberately no weighting field: the weight *is* the
    maximum score, which is how schools already think (20 + 20 + 60 = 100).
    """

    code = models.CharField(max_length=20, help_text="CA1, CA2, EXAM")
    name = models.CharField(max_length=60)
    max_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    order = models.PositiveSmallIntegerField(default=1)
    #: Null on both means the school-wide default set.
    level = models.ForeignKey(
        "academics.ClassLevel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="assessment_components",
    )
    subject = models.ForeignKey(
        "academics.Subject",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="assessment_components",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["code", "level", "subject"], name="unique_component_per_scope"
            )
        ]
        indexes = [models.Index(fields=["subject", "level", "is_active"])]

    def __str__(self) -> str:
        scope = self.subject or self.level
        return f"{self.code} ({self.max_score:g})" + (f" — {scope}" if scope else "")


class GradingScale(TimeStampedModel):
    """A set of bands, optionally scoped to one level.

    Junior and senior secondary classes are commonly graded differently in the
    same school, and a polytechnic grades on points where a secondary school
    does not, so the scale carries both the bands and whether grade points
    mean anything.
    """

    name = models.CharField(max_length=60, unique=True)
    #: Used when no scale matches the level. Exactly one; setting a new one clears the rest.
    is_default = models.BooleanField(default=False, db_index=True)
    level = models.ForeignKey(
        "academics.ClassLevel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="grading_scales",
    )
    pass_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("40.00"))
    #: Tertiary: bands carry grade points and GPA/CGPA are meaningful.
    uses_grade_points = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            GradingScale.objects.exclude(pk=self.pk).filter(is_default=True).update(
                is_default=False
            )

    def bands(self) -> list[Band]:
        """The scale as plain values, for the pure functions in `grading`."""
        return [
            Band(
                letter=row.letter,
                minimum=row.minimum,
                maximum=row.maximum,
                point=row.grade_point,
                remark=row.remark,
            )
            for row in self.grade_bands.all()
        ]


class GradeBand(TimeStampedModel):
    """75–100 → A1, 5.00 points, "Excellent"."""

    scale = models.ForeignKey(GradingScale, on_delete=models.CASCADE, related_name="grade_bands")
    letter = models.CharField(max_length=4)
    minimum = models.DecimalField(max_digits=5, decimal_places=2)
    maximum = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("100.00"))
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.00"))
    remark = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-minimum"]
        constraints = [
            models.UniqueConstraint(fields=["scale", "letter"], name="unique_band_per_scale")
        ]

    def __str__(self) -> str:
        return f"{self.letter} ({self.minimum:g}–{self.maximum:g})"

    def clean(self):
        if self.maximum < self.minimum:
            raise ValidationError({"maximum": "The upper bound cannot be below the lower one."})


class Score(TimeStampedModel):
    """One mark, for one component, of one registered subject.

    Anchored on `SubjectRegistration` rather than on (student, subject, term):
    a score for a course nobody registered is not a score, it is a data-entry
    accident, and the foreign key refuses it.

    `version` mirrors attendance — a teacher enters marks offline and syncs
    later, and a late sync must not overwrite an office correction silently.
    """

    registration = models.ForeignKey(
        "academics.SubjectRegistration", on_delete=models.CASCADE, related_name="scores"
    )
    component = models.ForeignKey(
        AssessmentComponent, on_delete=models.CASCADE, related_name="scores"
    )
    score = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    entered_by = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="scores_entered",
    )
    #: When the teacher keyed it in on the device; may predate the sync.
    recorded_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["component__order"]
        constraints = [
            models.UniqueConstraint(
                fields=["registration", "component"], name="unique_score_per_component"
            )
        ]
        indexes = [models.Index(fields=["registration"])]

    def __str__(self) -> str:
        return f"{self.registration} {self.component.code}: {self.score:g}"

    def clean(self):
        if self.score > self.component.max_score:
            raise ValidationError(
                {"score": f"{self.component.code} is marked out of {self.component.max_score:g}."}
            )


class SubjectResult(TimeStampedModel):
    """Derived: one subject, one term, one student.

    Rebuilt by `services.recompute_term`. Nothing writes to it by hand — a
    result that disagrees with the scores behind it is how a school loses an
    argument with a parent.
    """

    registration = models.OneToOneField(
        "academics.SubjectRegistration", on_delete=models.CASCADE, related_name="result"
    )
    total_score = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    max_total = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    grade = models.CharField(max_length=4, blank=True)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0.00"))
    #: Snapshot: a course's credit units may be re-weighted next session, and a
    #: transcript must keep printing what was actually earned.
    credit_units = models.PositiveSmallIntegerField(default=0)
    is_pass = models.BooleanField(default=False)
    #: True when a component has no score yet. Publishing is refused while any
    #: result in the term is incomplete.
    is_complete = models.BooleanField(default=False)

    position = models.PositiveSmallIntegerField(null=True, blank=True)
    cohort_size = models.PositiveSmallIntegerField(null=True, blank=True)
    class_average = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    class_highest = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    class_lowest = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    teacher_remark = models.CharField(max_length=60, blank=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["registration__subject__code"]
        indexes = [models.Index(fields=["grade", "is_pass"])]

    def __str__(self) -> str:
        return f"{self.registration} — {self.percentage:g}% {self.grade}"


class PromotionDecision(models.TextChoices):
    PENDING = "PENDING", "Not yet decided"
    PROMOTE = "PROMOTE", "Promote"
    REPEAT = "REPEAT", "Repeat the class"
    GRADUATE = "GRADUATE", "Graduate"


class TermResult(TimeStampedModel):
    """Derived: the whole term for one student.

    Carries the two numbers the school actually publishes — the average and
    the position — plus the GPA/CGPA where they mean anything, the attendance
    line that belongs on the same sheet, and the comments a form teacher and
    head write once results are computed.
    """

    enrolment = models.ForeignKey(
        "academics.Enrolment", on_delete=models.CASCADE, related_name="term_results"
    )
    term = models.ForeignKey("academics.Term", on_delete=models.CASCADE, related_name="results")

    subjects_count = models.PositiveSmallIntegerField(default=0)
    total_score = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    max_total = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    average = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    credit_units_registered = models.PositiveSmallIntegerField(default=0)
    credit_units_earned = models.PositiveSmallIntegerField(default=0)
    #: Null for a secondary school: no credit units, so no GPA to speak of.
    gpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    cgpa = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)

    position = models.PositiveSmallIntegerField(null=True, blank=True)
    cohort_size = models.PositiveSmallIntegerField(null=True, blank=True)

    days_present = models.PositiveSmallIntegerField(default=0)
    days_total = models.PositiveSmallIntegerField(default=0)

    form_teacher_comment = models.CharField(max_length=255, blank=True)
    head_comment = models.CharField(max_length=255, blank=True)
    promotion_status = models.CharField(
        max_length=10, choices=PromotionDecision.choices, default=PromotionDecision.PENDING
    )
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-term__session__start_date", "term__index", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["enrolment", "term"], name="one_result_per_enrolment_per_term"
            )
        ]
        indexes = [models.Index(fields=["term", "position"])]

    def __str__(self) -> str:
        return f"{self.enrolment.student} — {self.term} ({self.average:g}%)"

    @property
    def student(self):
        return self.enrolment.student

    @property
    def attendance_percentage(self) -> Decimal:
        if not self.days_total:
            return Decimal("0.00")
        from .grading import percentage

        return percentage(Decimal(self.days_present), Decimal(self.days_total))
