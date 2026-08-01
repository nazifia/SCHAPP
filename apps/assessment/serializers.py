from rest_framework import serializers

from apps.academics.models import AcademicSession, ClassArm, Subject, Term
from apps.api.mixins import ExpandableSerializerMixin

from .models import (
    AssessmentComponent,
    GradeBand,
    GradingScale,
    PromotionDecision,
    Score,
    SubjectResult,
    TermResult,
)


class AssessmentComponentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssessmentComponent
        fields = [
            "id",
            "code",
            "name",
            "max_score",
            "order",
            "level",
            "subject",
            "is_active",
            "updated_at",
        ]


class GradeBandSerializer(serializers.ModelSerializer):
    class Meta:
        model = GradeBand
        fields = ["id", "scale", "letter", "minimum", "maximum", "grade_point", "remark"]


class GradingScaleSerializer(serializers.ModelSerializer):
    grade_bands = GradeBandSerializer(many=True, read_only=True)

    class Meta:
        model = GradingScale
        fields = [
            "id",
            "name",
            "is_default",
            "level",
            "pass_percentage",
            "uses_grade_points",
            "grade_bands",
            "updated_at",
        ]


class ScoreSerializer(serializers.ModelSerializer):
    student = serializers.UUIDField(source="registration.enrolment.student_id", read_only=True)
    subject = serializers.UUIDField(source="registration.subject_id", read_only=True)

    class Meta:
        model = Score
        fields = [
            "id",
            "registration",
            "component",
            "score",
            "student",
            "subject",
            "version",
            "recorded_at",
            "updated_at",
        ]
        read_only_fields = ["version"]


class ScoreRowSerializer(serializers.Serializer):
    student = serializers.UUIDField()
    #: Null is an unmark — remove the stored mark. A blank field on a mark
    #: sheet is simply left out of the batch; only an explicit null deletes.
    score = serializers.DecimalField(max_digits=5, decimal_places=2, min_value=0, allow_null=True)
    #: The version the device last saw; omit when creating.
    version = serializers.IntegerField(required=False, allow_null=True)
    recorded_at = serializers.DateTimeField(required=False, allow_null=True)


class BulkScoreSerializer(serializers.Serializer):
    """A whole class, one component, one request."""

    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    subject = serializers.PrimaryKeyRelatedField(queryset=Subject.objects.all())
    component = serializers.PrimaryKeyRelatedField(queryset=AssessmentComponent.objects.all())
    class_arm = serializers.PrimaryKeyRelatedField(
        queryset=ClassArm.objects.all(), required=False, allow_null=True
    )
    rows = ScoreRowSerializer(many=True, allow_empty=False)


class SubjectResultSerializer(serializers.ModelSerializer):
    subject = serializers.UUIDField(source="registration.subject_id", read_only=True)
    subject_code = serializers.CharField(source="registration.subject.code", read_only=True)
    student = serializers.UUIDField(source="registration.enrolment.student_id", read_only=True)

    class Meta:
        model = SubjectResult
        fields = [
            "id",
            "registration",
            "student",
            "subject",
            "subject_code",
            "total_score",
            "max_total",
            "percentage",
            "grade",
            "grade_point",
            "credit_units",
            "is_pass",
            "is_complete",
            "position",
            "cohort_size",
            "class_average",
            "class_highest",
            "class_lowest",
            "teacher_remark",
            "updated_at",
        ]


class TermResultSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    student = serializers.UUIDField(source="enrolment.student_id", read_only=True)
    full_name = serializers.CharField(source="enrolment.student.full_name", read_only=True)
    attendance_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    #: Tertiary only, and null for a school: the course of study a result
    #: belongs to and the department that owns it. A phone showing a result has
    #: no other way to say which programme it is a result in.
    programme = serializers.SerializerMethodField()
    department = serializers.SerializerMethodField()

    def get_programme(self, obj) -> str | None:
        return str(obj.enrolment.programme) if obj.enrolment.programme_id else None

    def get_department(self, obj) -> str | None:
        return str(obj.enrolment.programme.department) if obj.enrolment.programme_id else None

    class Meta:
        model = TermResult
        fields = [
            "id",
            "enrolment",
            "student",
            "full_name",
            "programme",
            "department",
            "term",
            "subjects_count",
            "total_score",
            "max_total",
            "average",
            "credit_units_registered",
            "credit_units_earned",
            "gpa",
            "cgpa",
            "position",
            "cohort_size",
            "days_present",
            "days_total",
            "attendance_percentage",
            "form_teacher_comment",
            "head_comment",
            "promotion_status",
            "updated_at",
        ]
        # Everything derived is read-only; only the two comments are typed by a
        # human, and promotion moves through its own endpoint.
        read_only_fields = [
            field for field in fields if field not in {"form_teacher_comment", "head_comment"}
        ]


class ComputeTermSerializer(serializers.Serializer):
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())


class PublishTermSerializer(ComputeTermSerializer):
    force = serializers.BooleanField(default=False)


class PromotionOverrideSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=PromotionDecision.choices)
    reason = serializers.CharField(max_length=200)


class PromotionRunSerializer(serializers.Serializer):
    session = serializers.PrimaryKeyRelatedField(queryset=AcademicSession.objects.all())


class PromotionApplySerializer(PromotionRunSerializer):
    next_session = serializers.PrimaryKeyRelatedField(queryset=AcademicSession.objects.all())
