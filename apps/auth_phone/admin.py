from django.contrib import admin

from .models import OtpRequest


@admin.register(OtpRequest)
class OtpRequestAdmin(admin.ModelAdmin):
    """Why a code did not arrive — the single most common support question.

    Read-only, and `code_hash` is not among the fields shown: the hash is what
    verification compares against, so a support screen that displayed it would
    hand over every live code to whoever can read the page. Everything needed
    to diagnose a delivery failure is here without it.
    """

    list_display = (
        "created_at",
        "purpose",
        "user",
        "delivered",
        "delivery_provider",
        "delivery_status",
        "expires_at",
        "consumed_at",
        "attempts",
    )
    list_filter = ("purpose", "delivered", "channel", "delivery_provider")
    search_fields = ("delivery_message_id", "delivery_error")
    date_hierarchy = "created_at"
    fields = (
        "phone",
        "purpose",
        "user",
        "expires_at",
        "consumed_at",
        "invalidated_at",
        "attempts",
        "delivered",
        "delivery_provider",
        "delivery_message_id",
        "delivery_status",
        "delivery_error",
        "channel",
        "ip",
        "user_agent",
    )
    readonly_fields = fields

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
