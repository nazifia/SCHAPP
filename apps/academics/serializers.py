from rest_framework import serializers

from apps.api.mixins import ExpandableSerializerMixin

from . import services
from .models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Department,
    Enrolment,
    Faculty,
    Programme,
    Room,
    Stream,
    Subject,
    SubjectRegistration,
    TeachingAssignment,
    Term,
    TimetableEntry,
)


def _assert_dates_run_forwards(attrs, instance, label):
    """`Model.clean()` is not called by DRF, so this rule had no API half.

    Both models refuse `end_date <= start_date` in `clean()`, which the admin
    and `full_clean()` honour and a POST or PATCH never reached — the office
    could create a term that ended before it began, and every window check and
    date lookup downstream then read it as closed.

    A PATCH sends one bound, so the other has to come off the instance.
    """
    start = attrs.get("start_date") or getattr(instance, "start_date", None)
    end = attrs.get("end_date") or getattr(instance, "end_date", None)
    if start and end and end <= start:
        raise serializers.ValidationError({"end_date": f"The {label} must end after it starts."})
    return attrs


class AcademicSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicSession
        fields = ["id", "name", "start_date", "end_date", "is_current", "updated_at"]

    def validate(self, attrs):
        return _assert_dates_run_forwards(attrs, self.instance, "session")


class TermSerializer(serializers.ModelSerializer):
    accepts_scores = serializers.BooleanField(read_only=True)
    accepts_registration = serializers.BooleanField(read_only=True)
    results_published = serializers.BooleanField(read_only=True)
    # "First Term" repeats once a year, so a term picker listing seven of them
    # is seven identical rows. The session is what tells them apart.
    session_name = serializers.CharField(source="session.name", read_only=True)

    class Meta:
        model = Term
        fields = [
            "id",
            "session",
            "session_name",
            "index",
            "name",
            "start_date",
            "end_date",
            "registration_opens_at",
            "registration_closes_at",
            "result_entry_opens_at",
            "result_entry_closes_at",
            "results_published",
            "accepts_scores",
            "accepts_registration",
            "is_current",
            "updated_at",
        ]
        read_only_fields = ["results_published"]

    def validate(self, attrs):
        return _assert_dates_run_forwards(attrs, self.instance, "term")


class ClassLevelSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassLevel
        fields = ["id", "code", "name", "order", "next_level", "is_terminal", "updated_at"]


class StreamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stream
        fields = ["id", "code", "name", "updated_at"]


class FacultySerializer(serializers.ModelSerializer):
    class Meta:
        model = Faculty
        fields = ["id", "code", "name", "updated_at"]


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "faculty", "code", "name", "head", "updated_at"]


class ProgrammeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Programme
        fields = [
            "id",
            "department",
            "code",
            "name",
            "award",
            "duration_years",
            "min_credit_units",
            "max_credit_units",
            "is_active",
            "updated_at",
        ]


class ClassArmSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    label = serializers.CharField(source="__str__", read_only=True)
    #: Null unless the queryset went through `selectors.with_occupancy`, which
    #: needs a session to count against. Whoever is allocating a promoted year
    #: group needs the seats-left number in the same call as the arm list.
    enrolled = serializers.SerializerMethodField()
    seats_left = serializers.SerializerMethodField()
    expandable_fields = {"level": (ClassLevelSerializer, {}), "stream": (StreamSerializer, {})}

    class Meta:
        model = ClassArm
        fields = [
            "id",
            "level",
            "name",
            "label",
            "stream",
            "form_teacher",
            "capacity",
            "enrolled",
            "seats_left",
            "is_active",
            "updated_at",
        ]

    def get_enrolled(self, arm) -> int | None:
        return getattr(arm, "enrolled", None)

    def get_seats_left(self, arm) -> int | None:
        enrolled = getattr(arm, "enrolled", None)
        return None if enrolled is None else max(arm.capacity - enrolled, 0)


class SubjectSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        # Here rather than in `Subject.clean`: the relation is set after the
        # row is saved, so by the time model validation runs the list it would
        # have to check has not been assigned yet.
        #
        # Only on update: a subject being created has no row for anything else
        # to require yet, so its list cannot lead back to it.
        prerequisites = attrs.get("prerequisites")
        if prerequisites and self.instance is not None:
            services.assert_no_prerequisite_cycle(self.instance, prerequisites)
        return attrs

    class Meta:
        model = Subject
        fields = [
            "id",
            "code",
            "title",
            "category",
            "credit_units",
            "department",
            "level",
            "stream",
            "semester_offered",
            "prerequisites",
            "is_active",
            "updated_at",
        ]


class TeachingAssignmentSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    expandable_fields = {
        "subject": (SubjectSerializer, {}),
        "class_arm": (ClassArmSerializer, {}),
    }

    class Meta:
        model = TeachingAssignment
        fields = [
            "id",
            "staff",
            "subject",
            "session",
            "term",
            "class_arm",
            "level",
            "is_lead",
            "updated_at",
        ]

    def validate(self, attrs):
        if not attrs.get("class_arm") and not attrs.get("level"):
            raise serializers.ValidationError("Assign the subject to a class arm or a level.")
        return attrs


class EnrolmentSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    expandable_fields = {"class_arm": (ClassArmSerializer, {})}
    # A class list and an allocation worklist are read by a person deciding
    # where a child sits. Without these three every row is three UUIDs.
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_number = serializers.CharField(source="student.display_number", read_only=True)
    level_code = serializers.CharField(source="level.code", read_only=True)

    class Meta:
        model = Enrolment
        fields = [
            "id",
            "student",
            "student_name",
            "student_number",
            "session",
            "level",
            "level_code",
            "class_arm",
            "programme",
            "status",
            "roll_number",
            "promotion_note",
            "updated_at",
        ]
        read_only_fields = ["promotion_note"]


class SubjectRegistrationSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    expandable_fields = {"subject": (SubjectSerializer, {})}
    credit_units = serializers.IntegerField(source="subject.credit_units", read_only=True)
    # An approval queue is read by a person, and neither an enrolment id nor a
    # subject id tells them whose registration they are refusing.
    student_name = serializers.CharField(source="enrolment.student.full_name", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)

    class Meta:
        model = SubjectRegistration
        fields = [
            "id",
            "enrolment",
            "subject",
            "student_name",
            "subject_name",
            "term",
            "status",
            "is_carryover",
            "credit_units",
            "adviser_approved_at",
            "hod_approved_at",
            "rejection_reason",
            "updated_at",
        ]
        read_only_fields = [
            "status",
            "is_carryover",
            "adviser_approved_at",
            "hod_approved_at",
        ]


class RegisterSubjectsSerializer(serializers.Serializer):
    """Register a whole semester's courses in one call."""

    enrolment = serializers.PrimaryKeyRelatedField(queryset=Enrolment.objects.all())
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all())
    subjects = serializers.PrimaryKeyRelatedField(
        queryset=Subject.objects.all(), many=True, allow_empty=False
    )
    submit = serializers.BooleanField(default=True)


class AllocateToArmSerializer(serializers.Serializer):
    """Seat a batch of students in one class."""

    enrolments = serializers.PrimaryKeyRelatedField(
        queryset=Enrolment.objects.all(), many=True, allow_empty=False
    )
    #: Set only to overfill a class deliberately — a school that runs 45 to a
    #: room of 40 should not have to edit the capacity to record the truth.
    enforce_capacity = serializers.BooleanField(default=True)


class RejectRegistrationSerializer(serializers.Serializer):
    """A refusal the student can read. The reason is required on purpose —
    "rejected" with no explanation is a support call, not a decision."""

    reason = serializers.CharField(max_length=200, allow_blank=False)


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ["id", "code", "name", "capacity", "updated_at"]


class TimetableEntrySerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    expandable_fields = {
        "subject": (SubjectSerializer, {}),
        "class_arm": (ClassArmSerializer, {}),
        "room": (RoomSerializer, {}),
    }

    class Meta:
        model = TimetableEntry
        fields = [
            "id",
            "term",
            "day",
            "start_time",
            "end_time",
            "subject",
            "staff",
            "class_arm",
            "level",
            "room",
            "updated_at",
        ]
