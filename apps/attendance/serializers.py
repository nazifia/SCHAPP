from rest_framework import serializers

from apps.api.mixins import ExpandableSerializerMixin
from apps.people.serializers import StudentListSerializer

from .models import AttendanceStatus, StaffAttendance, StudentAttendance


class StudentAttendanceSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    expandable_fields = {"student": (StudentListSerializer, {})}

    class Meta:
        model = StudentAttendance
        fields = [
            "id",
            "student",
            "term",
            "date",
            "subject",
            "class_arm",
            "status",
            "method",
            "marked_by",
            "note",
            "version",
            "recorded_at",
            "updated_at",
        ]
        read_only_fields = ["version", "marked_by", "method"]


class AttendanceRowSerializer(serializers.Serializer):
    student = serializers.UUIDField()
    status = serializers.ChoiceField(choices=AttendanceStatus.choices)
    note = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    #: Version the device last saw. Omit when creating a fresh record.
    version = serializers.IntegerField(required=False, allow_null=True)
    #: When the teacher tapped it, not when the phone found signal.
    recorded_at = serializers.DateTimeField(required=False, allow_null=True)


class BulkAttendanceSerializer(serializers.Serializer):
    """A whole register in one request — the offline teacher's sync payload."""

    term = serializers.UUIDField()
    date = serializers.DateField()
    class_arm = serializers.UUIDField(required=False, allow_null=True)
    subject = serializers.UUIDField(required=False, allow_null=True)
    rows = AttendanceRowSerializer(many=True, allow_empty=False)

    def validate_rows(self, rows):
        seen = set()
        for row in rows:
            key = str(row["student"])
            if key in seen:
                raise serializers.ValidationError(f"Student {key} appears twice in this register.")
            seen.add(key)
        return rows


class StaffAttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = StaffAttendance
        fields = [
            "id",
            "staff",
            "date",
            "check_in",
            "check_out",
            "status",
            "method",
            "note",
            "updated_at",
        ]


class BiometricEventSerializer(serializers.Serializer):
    """Payload a gate terminal posts. Signature travels in a header."""

    external_id = serializers.CharField(max_length=64)
    occurred_at = serializers.DateTimeField()
    direction = serializers.ChoiceField(choices=["IN", "OUT"], default="IN")
    payload = serializers.JSONField(required=False, default=dict)


class AttendanceSummarySerializer(serializers.Serializer):
    student = serializers.UUIDField()
    full_name = serializers.CharField()
    present = serializers.IntegerField()
    absent = serializers.IntegerField()
    late = serializers.IntegerField()
    excused = serializers.IntegerField()
    total = serializers.IntegerField()
    percentage = serializers.FloatField()
