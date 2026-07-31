from rest_framework import serializers

from .models import AuditAction, AuditLog

#: `action` is a plain CharField, not a choices field — the trail has to keep
#: an action a later release stopped emitting, and a `choices` migration on an
#: append-only table for the sake of a label is not worth it. So the label is
#: resolved here, and an unknown code falls back to the code itself.
_LABELS = dict(AuditAction.choices)


class AuditLogSerializer(serializers.ModelSerializer):
    action_display = serializers.SerializerMethodField()

    def get_action_display(self, obj: AuditLog) -> str:
        return _LABELS.get(obj.action, obj.action)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "created_at",
            "action",
            "action_display",
            "actor_label",
            "object_type",
            "object_id",
            "summary",
            "before",
            "after",
            "ip",
            "device_id",
            "succeeded",
        ]
        read_only_fields = fields
