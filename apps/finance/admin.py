from django.contrib import admin

from .models import FeeItem, FeeStructure, Invoice, InvoiceLine, Payment


class FeeItemInline(admin.TabularInline):
    model = FeeItem
    extra = 0


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ["name", "session", "term", "level", "programme", "total", "is_active"]
    list_filter = ["is_active", "session"]
    inlines = [FeeItemInline]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "student", "term", "total", "amount_paid", "status", "due_date"]
    list_filter = ["status", "session", "term"]
    search_fields = ["number", "student__last_name", "student__admission_number"]
    raw_id_fields = ["student"]
    inlines = [InvoiceLineInline]
    #: Derived money is never typed in the admin — `recompute_paid` owns it.
    readonly_fields = ["number", "subtotal", "total", "amount_paid"]

    def has_delete_permission(self, request, obj=None) -> bool:
        """Cancel or waive a bill; never delete one. `services.cancel_invoice`
        is the way, and it refuses while successful payments are attached.

        The same rule `PaymentAdmin` states below, and it needs saying here
        too: `Invoice` is a `SoftDeleteModel`, but the admin's bulk
        `delete_selected` runs through Django's collector rather than
        `Model.delete()`, so the soft-delete override does not see it.
        """
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["reference", "invoice", "amount", "method", "status", "gateway", "paid_at"]
    list_filter = ["status", "method", "gateway"]
    search_fields = ["reference", "gateway_reference", "invoice__number"]
    raw_id_fields = ["invoice"]
    readonly_fields = ["reference", "gateway_reference", "raw"]

    def has_delete_permission(self, request, obj=None) -> bool:
        """Reverse a payment; never delete one. A receipt is out in the world."""
        return False
