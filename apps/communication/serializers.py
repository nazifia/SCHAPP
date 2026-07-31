from rest_framework import serializers

from .models import Announcement, Channel, Message, MessageTemplate


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = ["id", "code", "name", "channel", "subject", "body", "is_active", "updated_at"]


class AnnouncementSerializer(serializers.ModelSerializer):
    author_name = serializers.CharField(source="author.full_name", read_only=True)
    is_published = serializers.BooleanField(read_only=True)

    class Meta:
        model = Announcement
        fields = [
            "id",
            "title",
            "body",
            "audience_roles",
            "level",
            "class_arm",
            "channels",
            "author",
            "author_name",
            "published_at",
            "expires_at",
            "is_pinned",
            "is_published",
            "recipients_count",
            "updated_at",
        ]
        read_only_fields = ["author", "published_at", "recipients_count"]

    def validate_channels(self, value):
        allowed = {Channel.SMS, Channel.EMAIL, Channel.PUSH}
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(f"Unknown channel(s): {sorted(unknown)}")
        return value


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = [
            "id",
            "channel",
            "destination",
            "subject",
            "body",
            "user",
            "announcement",
            "status",
            "provider",
            "delivery_status",
            "error",
            "sent_at",
            "delivered_at",
            "read_at",
            "created_at",
        ]


class BulkSmsSerializer(serializers.Serializer):
    """Free-text SMS to a list of numbers. Phones are normalised server-side."""

    phones = serializers.ListField(child=serializers.CharField(max_length=20), allow_empty=False)
    body = serializers.CharField(max_length=800)


class DeliveryReportSerializer(serializers.Serializer):
    message_id = serializers.CharField(max_length=120)
    status = serializers.CharField(max_length=30)
