from django.contrib import admin

from .models import IdempotencyRecord


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(admin.ModelAdmin):
    """A support view of replayed writes. Read-only, and deliberately so:
    editing a claimed key by hand is how you get the duplicate write the table
    exists to prevent. Deleting one is allowed — a key stuck with
    ``completed_at`` unset after a crash is a client permanently locked out of
    retrying, and releasing it is the fix."""

    list_display = ("created_at", "method", "path", "status_code", "completed_at", "key")
    list_filter = ("method", "status_code")
    search_fields = ("key", "path")
    date_hierarchy = "created_at"
    readonly_fields = [f.name for f in IdempotencyRecord._meta.fields]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
