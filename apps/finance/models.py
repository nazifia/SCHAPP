"""Fees, invoices and the money that comes back against them.

Three rules shape everything here, and all three come from the way school fees
are actually paid in Nigeria:

* **an invoice is a bill, not a balance.** A parent pays ₦40,000 of a ₦120,000
  bill in September and the rest in November, from two different phones,
  sometimes under a sibling's name. So `Invoice.amount_paid` is derived from
  the payments, never typed in, and `recompute_paid()` is the only writer;
* **a payment arrives more than once.** A gateway retries its webhook, a
  bursar clicks verify while the webhook is in flight, a parent refreshes the
  checkout page. `Payment.gateway_reference` is unique, which turns every one
  of those into the same single row instead of crediting the fee twice;
* **money is never deleted.** `Invoice` is soft-deleted and a `Payment` is
  reversed with a reason, because a receipt a parent is holding must still be
  explainable a year later.

Amounts are `Decimal` in naira, two places. There is no per-tenant currency:
these are Nigerian institutions and `settings.CURRENCY_CODE` is NGN.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum

from apps.common.models import SoftDeleteModel, TimeStampedModel

ZERO = Decimal("0.00")


class FeeCategory(models.TextChoices):
    TUITION = "TUITION", "Tuition"
    DEVELOPMENT = "DEVELOPMENT", "Development levy"
    EXAMINATION = "EXAMINATION", "Examination"
    ACCEPTANCE = "ACCEPTANCE", "Acceptance"
    HOSTEL = "HOSTEL", "Hostel / accommodation"
    TRANSPORT = "TRANSPORT", "Transport"
    UNIFORM = "UNIFORM", "Uniform and books"
    PTA = "PTA", "PTA / association dues"
    OTHER = "OTHER", "Other"


class FeeStructure(TimeStampedModel):
    """What one cohort is charged for one term.

    Scoped the same way assessment components are — session and term always,
    then optionally a level or a programme — so a school can price SSS3
    separately from JSS1 without a structure per class arm.
    """

    name = models.CharField(max_length=120)
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.CASCADE, related_name="fee_structures"
    )
    #: Null means the charge covers the whole session and is billed once.
    term = models.ForeignKey(
        "academics.Term",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="fee_structures",
    )
    level = models.ForeignKey(
        "academics.ClassLevel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="fee_structures",
    )
    programme = models.ForeignKey(
        "academics.Programme",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="fee_structures",
    )
    #: Days after issue that the bill falls due; the invoice snapshots the date.
    due_in_days = models.PositiveSmallIntegerField(default=21)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        ordering = ["-session__start_date", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "term", "level", "programme", "name"],
                name="unique_fee_structure_per_scope",
            )
        ]
        indexes = [models.Index(fields=["session", "term", "is_active"])]

    def __str__(self) -> str:
        scope = self.programme or self.level
        return f"{self.name}" + (f" — {scope}" if scope else "")

    @property
    def total(self) -> Decimal:
        """Compulsory items only: the optional ones are opt-in per student."""
        return self.items.filter(is_optional=False).aggregate(t=Sum("amount"))["t"] or ZERO


class FeeItem(TimeStampedModel):
    """One line of a fee structure: "Tuition ₦85,000"."""

    structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=120)
    category = models.CharField(
        max_length=20, choices=FeeCategory.choices, default=FeeCategory.OTHER
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    #: Hostel, bus, lunch — charged only to students who take them.
    is_optional = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.amount:,.2f})"


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    PART_PAID = "PART_PAID", "Partly paid"
    PAID = "PAID", "Paid"
    WAIVED = "WAIVED", "Waived"
    CANCELLED = "CANCELLED", "Cancelled"


#: Statuses a payment may still be applied to.
OPEN_INVOICE_STATUSES = {InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID, InvoiceStatus.DRAFT}


class Invoice(SoftDeleteModel):
    """One bill, for one student, for one term.

    Lines are snapshotted off the fee structure at generation time rather than
    read through it: a school that raises tuition in January must not silently
    change every invoice it issued in September.
    """

    number = models.CharField(max_length=40, unique=True, db_index=True)
    student = models.ForeignKey("people.Student", on_delete=models.PROTECT, related_name="invoices")
    session = models.ForeignKey(
        "academics.AcademicSession", on_delete=models.PROTECT, related_name="invoices"
    )
    term = models.ForeignKey(
        "academics.Term", null=True, blank=True, on_delete=models.PROTECT, related_name="invoices"
    )
    structure = models.ForeignKey(
        FeeStructure, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoices"
    )

    status = models.CharField(
        max_length=12, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT, db_index=True
    )
    #: Sum of the lines. Written by `recompute_totals`, not by hand.
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    discount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)]
    )
    total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    #: Sum of successful payments. Written by `recompute_paid`, not by hand.
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)

    issued_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    waiver_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # One live bill per student per structure: re-running generation
            # for a cohort tops up the students who were added and skips the
            # rest.
            #
            # This one *has* to be conditional — a soft-deleted invoice must
            # not block re-billing — which means MySQL drops it and only SQLite
            # enforces it. `services.generate_invoices` therefore does the real
            # work, reading the existing invoices inside the same transaction;
            # the constraint is a second pair of eyes in development, not the
            # guarantee. (Contrast `Payment.gateway_reference`, where the
            # guarantee is the point and the constraint is unconditional.)
            models.UniqueConstraint(
                fields=["student", "structure"],
                condition=models.Q(deleted_at__isnull=True, structure__isnull=False),
                name="one_invoice_per_student_per_structure",
            )
        ]
        indexes = [
            models.Index(fields=["status", "due_date"]),
            models.Index(fields=["student", "session"]),
        ]

    def __str__(self) -> str:
        return f"{self.number} — {self.student} ({self.total:,.2f})"

    @property
    def balance(self) -> Decimal:
        return self.total - self.amount_paid

    @property
    def is_settled(self) -> bool:
        return self.status in {InvoiceStatus.PAID, InvoiceStatus.WAIVED}

    @property
    def is_payable(self) -> bool:
        return self.status in OPEN_INVOICE_STATUSES and self.balance > ZERO

    def recompute_totals(self) -> "Invoice":
        self.subtotal = self.lines.aggregate(t=Sum("amount"))["t"] or ZERO
        self.total = max(self.subtotal - self.discount, ZERO)
        return self

    def recompute_paid(self) -> "Invoice":
        """Re-derive `amount_paid` and the status from the payments.

        A rebuild, not an increment, for the same reason `recompute_term` is:
        a reversal two weeks later has to land on the right number without
        anyone remembering what the old one was.
        """
        self.amount_paid = (
            self.payments.filter(status=PaymentStatus.SUCCESS).aggregate(t=Sum("amount"))["t"]
            or ZERO
        )
        if self.status in {InvoiceStatus.CANCELLED, InvoiceStatus.WAIVED}:
            return self
        if self.amount_paid >= self.total and self.total > ZERO:
            self.status = InvoiceStatus.PAID
        elif self.amount_paid > ZERO:
            self.status = InvoiceStatus.PART_PAID
        elif self.issued_at:
            self.status = InvoiceStatus.ISSUED
        else:
            # Back to DRAFT, and this branch is not decorative: a draft is
            # payable (`OPEN_INVOICE_STATUSES`), so a bill paid at the counter
            # before anyone issued it becomes PAID. Without this line, reversing
            # that payment left the status on PAID with the whole balance
            # outstanding — and PAID is not payable, so the invoice was stuck
            # for good.
            self.status = InvoiceStatus.DRAFT
        return self


class InvoiceLine(TimeStampedModel):
    """A snapshotted charge. `fee_item` is a breadcrumb, not the source."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=160)
    category = models.CharField(
        max_length=20, choices=FeeCategory.choices, default=FeeCategory.OTHER
    )
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )
    fee_item = models.ForeignKey(
        FeeItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="invoice_lines"
    )

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.description} {self.amount:,.2f}"


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    TRANSFER = "TRANSFER", "Bank transfer"
    POS = "POS", "POS terminal"
    CHEQUE = "CHEQUE", "Cheque"
    ONLINE = "ONLINE", "Online (card / transfer / USSD)"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Awaiting confirmation"
    SUCCESS = "SUCCESS", "Successful"
    FAILED = "FAILED", "Failed"
    REVERSED = "REVERSED", "Reversed"


class Payment(TimeStampedModel):
    """Money against an invoice, however it arrived.

    One table for the bursar's cash receipt and the gateway's card charge,
    because a parent's statement has to show both and a reconciliation that
    reads two tables reconciles neither.
    """

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    #: Our receipt number. Also what we hand the gateway as its reference, so
    #: one string identifies the charge on both sides of the conversation.
    reference = models.CharField(max_length=40, unique=True, db_index=True)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(
        max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    status = models.CharField(
        max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True
    )

    gateway = models.CharField(max_length=20, blank=True, help_text="paystack, flutterwave")
    #: The gateway's own id for the transaction, and the whole of the
    #: idempotency guarantee against a replayed webhook.
    #:
    #: NULL rather than "" when there is none, because the unique constraint
    #: below has to be unconditional: MySQL silently *drops* a UniqueConstraint
    #: that carries a `condition`, so a partial index would leave production
    #: with no protection at all while SQLite tests passed. NULLs do not
    #: collide on either engine, which is the portable way to say "unique
    #: where present".
    gateway_reference = models.CharField(  # noqa: DJ001 - NULL is the constraint
        max_length=100, null=True, blank=True, default=None
    )

    paid_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        "people.Staff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payments_recorded",
    )
    payer_name = models.CharField(max_length=120, blank=True)
    payer_phone = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=255, blank=True)
    reversal_reason = models.CharField(max_length=255, blank=True)
    #: Whatever the gateway echoed back, kept for the argument nobody expects.
    raw = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["gateway", "gateway_reference"],
                name="one_payment_per_gateway_reference",
            )
        ]
        indexes = [models.Index(fields=["status", "paid_at"])]

    def __str__(self) -> str:
        return f"{self.reference} {self.amount:,.2f} ({self.get_status_display()})"

    def clean(self):
        if self.status == PaymentStatus.REVERSED and not self.reversal_reason:
            raise ValidationError({"reversal_reason": "Say why the payment was reversed."})
