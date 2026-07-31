from rest_framework import serializers

from apps.api.mixins import ExpandableSerializerMixin

from .models import (
    Application,
    Guardian,
    Staff,
    Student,
    StudentDocument,
    StudentGuardian,
)


class StudentImportSerializer(serializers.Serializer):
    """A CSV upload. `session` places the intake in a cohort as it lands."""

    file = serializers.FileField()
    session = serializers.UUIDField(
        required=False, help_text="Enrol the imported students into this session."
    )
    #: An intake of forty into an arm sized thirty is a data problem, not a
    #: capacity decision — but a school importing history into last year's
    #: classes needs to be able to say so.
    enforce_capacity = serializers.BooleanField(default=True)


class GuardianSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Guardian
        fields = [
            "id",
            "first_name",
            "last_name",
            "other_name",
            "full_name",
            "phone",
            "email",
            "occupation",
            "address",
            "updated_at",
        ]


class StudentGuardianSerializer(serializers.ModelSerializer):
    guardian = GuardianSerializer(read_only=True)

    class Meta:
        model = StudentGuardian
        fields = ["id", "guardian", "relationship", "is_primary", "can_pick_up"]


class StudentListSerializer(serializers.ModelSerializer):
    """Class lists and search results.

    No NIN, no medical notes, no address — a list endpoint is the wrong place
    to hand out a child's personal data in bulk.
    """

    full_name = serializers.CharField(read_only=True)
    display_number = serializers.CharField(read_only=True)

    class Meta:
        model = Student
        fields = [
            "id",
            "full_name",
            "display_number",
            "admission_number",
            "gender",
            "status",
            "current_level",
            "current_arm",
            "photo",
            "updated_at",
        ]


class StudentSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    display_number = serializers.CharField(read_only=True)
    nin = serializers.CharField(source="masked_nin", read_only=True)
    guardians = StudentGuardianSerializer(source="guardian_links", many=True, read_only=True)

    class Meta:
        model = Student
        fields = [
            "id",
            "admission_number",
            "matriculation_number",
            "first_name",
            "last_name",
            "other_name",
            "full_name",
            "display_number",
            "gender",
            "date_of_birth",
            "phone",
            "email",
            "address",
            "state_of_origin",
            "lga",
            "photo",
            "nin",
            "status",
            "date_admitted",
            "current_level",
            "current_arm",
            "programme",
            "stream",
            "blood_group",
            "medical_notes",
            "religion",
            "guardians",
            "updated_at",
        ]
        read_only_fields = ["admission_number", "matriculation_number", "nin"]


class LinkGuardianSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=24)
    first_name = serializers.CharField(max_length=60, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=60, required=False, allow_blank=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    occupation = serializers.CharField(max_length=80, required=False, allow_blank=True)
    relationship = serializers.CharField(max_length=10, default="GUARDIAN")
    is_primary = serializers.BooleanField(default=False)


class StaffSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    nin = serializers.CharField(source="masked_nin", read_only=True)
    #: The roles are the account's, not the staff record's — a personnel file
    #: has no permissions. Read-only here; `POST staff/{id}/account/` is the
    #: only way to change them, so granting a power is always one audited act.
    account_roles = serializers.SerializerMethodField()

    class Meta:
        model = Staff
        fields = [
            "id",
            "user",
            "account_roles",
            "staff_number",
            "first_name",
            "last_name",
            "other_name",
            "full_name",
            "gender",
            "phone",
            "email",
            "photo",
            "nin",
            "staff_type",
            "designation",
            "department",
            "qualification",
            "date_employed",
            "status",
            "updated_at",
        ]
        read_only_fields = ["staff_number", "nin", "user"]

    def get_account_roles(self, obj: Staff) -> list[str]:
        user = obj.user
        if user is None:
            return []
        return sorted(user.roles.values_list("code", flat=True))


class StaffListSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    #: So the staff list can show who can actually sign in without N+1 detail
    #: fetches — the whole point of the account screen is spotting the gaps.
    has_account = serializers.SerializerMethodField()

    class Meta:
        model = Staff
        fields = [
            "id",
            "staff_number",
            "full_name",
            "designation",
            "department",
            "status",
            "has_account",
        ]

    def get_has_account(self, obj: Staff) -> bool:
        return obj.user_id is not None


class StaffAccountSerializer(serializers.Serializer):
    """Give this staff member a login, with these roles."""

    roles = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class ApplicationSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = Application
        fields = [
            "id",
            "reference",
            "first_name",
            "last_name",
            "other_name",
            "full_name",
            "gender",
            "date_of_birth",
            "phone",
            "email",
            "address",
            "state_of_origin",
            "lga",
            "session",
            "level_applied",
            "programme_applied",
            "previous_school",
            "guardian_name",
            "guardian_phone",
            "status",
            "screening_score",
            "screening_notes",
            "offered_at",
            "accepted_at",
            "acceptance_fee_paid",
            "student",
            "updated_at",
        ]
        read_only_fields = ["reference", "status", "student", "offered_at", "accepted_at"]


class ApplicationTransitionSerializer(serializers.Serializer):
    status = serializers.CharField()
    notes = serializers.CharField(required=False, allow_blank=True)
    score = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, allow_null=True
    )


class ConvertApplicationSerializer(serializers.Serializer):
    session = serializers.UUIDField(required=False, allow_null=True)
    class_arm = serializers.UUIDField(required=False, allow_null=True)


class StudentDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentDocument
        fields = ["id", "student", "title", "file", "uploaded_by", "created_at"]
        read_only_fields = ["uploaded_by"]
