from django.contrib import admin

from .models import BiometricDevice, BiometricEvent, StaffAttendance, StudentAttendance


@admin.register(StudentAttendance)
class StudentAttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "subject", "status", "method", "marked_by")
    list_filter = ("status", "method", "date", "class_arm")
    date_hierarchy = "date"
    search_fields = ("student__first_name", "student__last_name")


@admin.register(StaffAttendance)
class StaffAttendanceAdmin(admin.ModelAdmin):
    list_display = ("staff", "date", "check_in", "check_out", "status", "method")
    list_filter = ("status", "method", "date")


@admin.register(BiometricDevice)
class BiometricDeviceAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "location", "is_active", "last_seen_at")
    # The shared secret is write-only: showing it in a list view defeats it.
    exclude = ("secret",)


@admin.register(BiometricEvent)
class BiometricEventAdmin(admin.ModelAdmin):
    list_display = (
        "device",
        "external_id",
        "occurred_at",
        "direction",
        "processed_at",
        "match_error",
    )
    list_filter = ("device", "direction")
    readonly_fields = ("payload",)
