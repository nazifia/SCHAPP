from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Device, Role, TokenFamily, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["last_name", "first_name"]
    list_display = ("phone_display", "full_name", "email", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "roles")
    search_fields = ("phone", "phone_display", "first_name", "last_name", "email")
    filter_horizontal = ("roles", "groups", "user_permissions")
    # NIN and pin_hash are never editable or visible here.
    readonly_fields = ("last_login", "phone_verified_at", "pin_set_at", "masked_nin")
    fieldsets = (
        (None, {"fields": ("phone", "phone_display", "password")}),
        ("Personal", {"fields": ("first_name", "last_name", "other_name", "email", "photo")}),
        ("Identity", {"fields": ("masked_nin", "nin_verified_at", "phone_verified_at")}),
        ("Access", {"fields": ("roles", "is_active", "is_staff", "is_superuser", "pin_set_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("phone", "first_name", "last_name", "password1", "password2"),
            },
        ),
    )


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_system")
    list_filter = ("is_system",)


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "name", "platform", "last_seen_at", "revoked_at")
    list_filter = ("platform",)


@admin.register(TokenFamily)
class TokenFamilyAdmin(admin.ModelAdmin):
    list_display = ("user", "device", "created_at", "last_used_at", "revoked_at", "revoked_reason")
    list_filter = ("revoked_reason",)
