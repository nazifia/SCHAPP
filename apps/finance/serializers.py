from decimal import Decimal

from rest_framework import serializers

from apps.academics.models import AcademicSession, Term
from apps.api.mixins import ExpandableSerializerMixin

from .models import (
    FeeCategory,
    FeeItem,
    FeeStructure,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentMethod,
)


class FeeItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeItem
        fields = [
            "id",
            "structure",
            "name",
            "category",
            "amount",
            "is_optional",
            "order",
            "updated_at",
        ]


class FeeStructureSerializer(serializers.ModelSerializer):
    items = FeeItemSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = FeeStructure
        fields = [
            "id",
            "name",
            "session",
            "term",
            "level",
            "programme",
            "due_in_days",
            "is_active",
            "items",
            "total",
            "updated_at",
        ]


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = ["id", "invoice", "description", "category", "amount", "fee_item"]


class InvoiceSerializer(ExpandableSerializerMixin, serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    full_name = serializers.CharField(source="student.full_name", read_only=True)
    admission_number = serializers.CharField(source="student.display_number", read_only=True)
    balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "student",
            "full_name",
            "admission_number",
            "session",
            "term",
            "structure",
            "status",
            "subtotal",
            "discount",
            "total",
            "amount_paid",
            "balance",
            "issued_at",
            "due_date",
            "note",
            "waiver_reason",
            "lines",
            "updated_at",
        ]
        # Everything about the money is derived or moves through an action.
        read_only_fields = [
            "number",
            "status",
            "subtotal",
            "total",
            "amount_paid",
            "issued_at",
            "waiver_reason",
        ]

    def update(self, instance, validated_data):
        """Re-derive the totals when the discount moves.

        `discount` is the one writable field the money depends on, and
        `recompute_totals` — the only thing that reads it — ran in exactly two
        places, neither reachable from here: invoice generation, and
        `issue_invoice`, which returns early for anything past DRAFT. So a
        bursar discounting an issued bill wrote the column and changed nothing:
        `total` and `balance` stayed put and the parent still owed the full
        amount, with a discount showing on the record.

        `recompute_paid` follows because the status depends on the new total —
        a part-paid bill discounted down to what has already been paid is PAID,
        and without this it stays PART_PAID with a negative balance, which
        `_assert_payable` then refuses any further payment against.
        """
        invoice = super().update(instance, validated_data)
        if "discount" in validated_data:
            invoice.recompute_totals().recompute_paid()
            invoice.save(update_fields=["subtotal", "total", "amount_paid", "status", "updated_at"])
        return invoice


class PaymentSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    full_name = serializers.CharField(source="invoice.student.full_name", read_only=True)

    class Meta:
        model = Payment
        fields = [
            "id",
            "invoice",
            "invoice_number",
            "full_name",
            "reference",
            "amount",
            "method",
            "status",
            "gateway",
            "paid_at",
            "payer_name",
            "payer_phone",
            "note",
            "reversal_reason",
            "updated_at",
        ]
        read_only_fields = ["reference", "status", "gateway", "paid_at", "reversal_reason"]


class GenerateInvoicesSerializer(serializers.Serializer):
    structure = serializers.PrimaryKeyRelatedField(queryset=FeeStructure.objects.all())
    #: Omit to bill the whole cohort the structure is scoped to.
    students = serializers.ListField(child=serializers.UUIDField(), required=False)


class RecordPaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    payer_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    payer_phone = serializers.CharField(max_length=20, required=False, allow_blank=True)
    note = serializers.CharField(max_length=255, required=False, allow_blank=True)


class CheckoutSerializer(serializers.Serializer):
    gateway = serializers.CharField(max_length=20)
    #: Part payment. Omit to pay the whole outstanding balance.
    amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal("0.01")
    )
    email = serializers.EmailField(required=False, allow_blank=True)
    callback_url = serializers.URLField(required=False, allow_blank=True)


class ReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class AdjustLineSerializer(serializers.Serializer):
    """A line as an authority wants it to read, not as it was snapshotted."""

    description = serializers.CharField(max_length=160)
    category = serializers.ChoiceField(choices=FeeCategory.choices, default=FeeCategory.OTHER)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))
    #: Send back what the invoice already carries to keep the breadcrumb to the
    #: price list; omit it on a line invented for this one student.
    fee_item = serializers.PrimaryKeyRelatedField(
        queryset=FeeItem.objects.all(), required=False, allow_null=True
    )


class AdjustInvoiceSerializer(serializers.Serializer):
    """`lines` is the whole bill after the edit, not the delta."""

    lines = AdjustLineSerializer(many=True)
    discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, min_value=Decimal("0.00")
    )
    reason = serializers.CharField(max_length=255)


class FinanceReportSerializer(serializers.Serializer):
    session = serializers.PrimaryKeyRelatedField(
        queryset=AcademicSession.objects.all(), required=False
    )
    term = serializers.PrimaryKeyRelatedField(queryset=Term.objects.all(), required=False)
