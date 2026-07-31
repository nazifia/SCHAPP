from rest_framework import serializers

from .models import Device, Role, User


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["code", "name", "description", "permissions", "is_system"]
        read_only_fields = ["is_system"]


class DeviceSerializer(serializers.ModelSerializer):
    is_current = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [
            "id",
            "device_id",
            "name",
            "platform",
            "app_version",
            "last_seen_at",
            "revoked_at",
            "is_current",
        ]
        read_only_fields = fields

    def get_is_current(self, obj: Device) -> bool:
        return obj.device_id == self.context.get("current_device_id")


class UserSerializer(serializers.ModelSerializer):
    """Detail view of the signed-in user.

    NIN appears masked and only here — never in any list endpoint.
    """

    full_name = serializers.CharField(read_only=True)
    roles = serializers.SlugRelatedField(slug_field="code", many=True, read_only=True)
    permissions = serializers.SerializerMethodField()
    nin = serializers.CharField(source="masked_nin", read_only=True)
    has_pin = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "phone_display",
            "first_name",
            "last_name",
            "other_name",
            "full_name",
            "email",
            "photo",
            "nin",
            "roles",
            "permissions",
            "has_pin",
            "is_active",
            "phone_verified_at",
        ]
        read_only_fields = fields

    def get_permissions(self, obj: User) -> list[str]:
        return sorted(obj.permission_codes)

    def get_has_pin(self, obj: User) -> bool:
        return bool(obj.pin_hash)


class UserListSerializer(serializers.ModelSerializer):
    """Deliberately narrow: no NIN, no email, no phone in E.164."""

    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = ["id", "full_name", "phone_display", "is_active"]
        read_only_fields = fields
