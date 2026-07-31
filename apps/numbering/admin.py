from django.contrib import admin

from .models import MobileNumberAllocation
from .selectors import invalidate_allocation_cache


@admin.register(MobileNumberAllocation)
class MobileNumberAllocationAdmin(admin.ModelAdmin):
    list_display = (
        "ndc",
        "operator",
        "status",
        "nsn_length",
        "allows_user_accounts",
        "last_verified_at",
    )
    list_filter = ("operator", "status", "allows_user_accounts")
    search_fields = ("ndc", "notes")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        invalidate_allocation_cache()

    def delete_model(self, request, obj):
        super().delete_model(request, obj)
        invalidate_allocation_cache()
