from django.contrib import admin

from .models import Announcement, Message, MessageTemplate


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "channel", "is_active"]
    list_filter = ["channel", "is_active"]


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ["title", "level", "class_arm", "published_at", "recipients_count", "is_pinned"]
    list_filter = ["is_pinned", "level"]
    search_fields = ["title", "body"]
    readonly_fields = ["published_at", "recipients_count"]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    """The send log. Read-only: it is a record of what happened."""

    list_display = ["created_at", "channel", "destination", "status", "provider", "delivered_at"]
    list_filter = ["channel", "status"]
    search_fields = ["destination", "provider_message_id"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
