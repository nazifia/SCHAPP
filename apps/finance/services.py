"""Raising bills and taking money for them.

The order a term's money actually moves in:

1. **generation** — a bursar points a fee structure at a cohort and gets one
   invoice per student, lines snapshotted, in one transaction. Re-running it
   after ten more students are admitted bills those ten and nobody twice;
2. **collection** — cash at the counter goes in through `record_payment`;
   a card or transfer goes out through `start_online_payment` and comes back
   through `confirm_payment`;
3. **reconciliation** — `confirm_payment` is the *only* place a payment is
   marked successful, whether the news arrived by webhook, by a bursar
   clicking verify, or by the nightly sweep. One path means one set of rules
   about amounts, duplicates and already-settled invoices.

Every write here is idempotent, because every one of them will happen twice.
"""

import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from apps.academics.models import Enrolment, EnrolmentStatus
from apps.api.bulk import BulkOutcome, RowError, atomic_bulk
from apps.api.exceptions import AppError
from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.people.numbering import NumberContext, next_number
from apps.tenants.db import tenant_atomic

from .gateways import Verification, get_gateway
from .models import (
    ZERO,
    FeeItem,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
)

logger = logging.getLogger(__name__)

INVOICE_NUMBER_FORMAT = "INV/{yy}/{serial}"
RECEIPT_NUMBER_FORMAT = "RCT/{yy}/{serial}"


class FinanceError(AppError):
    default_code = "FINANCE_ERROR"


class InvoiceNotPayable(FinanceError):
    default_code = "INVOICE_NOT_PAYABLE"
    default_detail = "This invoice is cancelled, waived or already settled."


class PaymentAmountInvalid(FinanceError):
    default_code = "PAYMENT_AMOUNT_INVALID"
    default_detail = "The amount must be positive and no more than the outstanding balance."


class NothingToBill(FinanceError):
    default_code = "NOTHING_TO_BILL"
    default_detail = "This fee structure has no compulsory items."


# ---------------------------------------------------------------------------
# Editing the price list
# ---------------------------------------------------------------------------
def _resync_draft_lines(item: FeeItem) -> int:
    """Carry a re-priced item into the bills nobody has issued yet.

    The snapshot rule cuts both ways. An *issued* bill must never move under a
    parent, which is why `InvoiceLine` copies the amount — but a DRAFT is not a
    bill yet, nobody has seen it, and leaving it on last week's price means the
    bursar who corrects a typo before issuing has to delete and regenerate the
    whole cohort to make the correction stick.

    ponytail: existing lines only. An item *added* to a structure after
    generation does not appear on drafts already raised — regenerate for that.
    """
    lines = InvoiceLine.objects.filter(
        fee_item=item, invoice__status=InvoiceStatus.DRAFT, invoice__deleted_at__isnull=True
    )
    invoice_ids = list(lines.values_list("invoice_id", flat=True).distinct())
    if not invoice_ids:
        return 0
    # `.update()` skips `auto_now`, so `updated_at` is written by hand — offline
    # clients sync on it and would otherwise never see the new price.
    lines.update(
        description=item.name, category=item.category, amount=item.amount, updated_at=timezone.now()
    )
    for invoice in Invoice.objects.filter(pk__in=invoice_ids):
        invoice.recompute_totals()
        invoice.save(update_fields=["subtotal", "total", "updated_at"])
    return len(invoice_ids)


@tenant_atomic()
def fee_changed(obj, *, before: dict | None = None, actor=None, request=None) -> int:
    """Record a price-list edit, and resync the drafts it affects.

    Called after a `FeeStructure` or `FeeItem` is written. A fee change is the
    single most disputed number in a Nigerian school's year — "the school
    raised tuition after I paid" — so who changed what, from what, to what is
    kept whether or not anything was billed from it. Returns the number of
    draft invoices repriced.
    """
    is_item = isinstance(obj, FeeItem)
    resynced = _resync_draft_lines(obj) if is_item else 0
    after = (
        {"name": obj.name, "amount": str(obj.amount), "is_optional": obj.is_optional}
        if is_item
        else {"name": obj.name, "is_active": obj.is_active, "due_in_days": obj.due_in_days}
    )
    record(
        AuditAction.FEE_CHANGED,
        request=request,
        actor=actor,
        obj=obj,
        before=before,
        after=after,
        summary=f"{obj} ({resynced} draft invoices repriced)"[:255],
    )
    return resynced


@tenant_atomic()
def adjust_invoice(
    invoice: Invoice, *, lines: list[dict], reason: str, discount=None, actor=None, request=None
) -> Invoice:
    """Rewrite one student's bill: its lines, and optionally its discount.

    The per-student escape hatch the price list cannot express — a sibling
    rebate, a boarder who moved to day, a levy charged in error. `lines` is the
    full replacement set, not a patch, so two authorities adjusting the same
    bill converge on whichever list was sent last rather than compounding each
    other's edits.

    Two things it refuses: a settled bill (cancelled or waived is a decision,
    not a draft), and an adjustment that drops the total below what has already
    been paid — that is a refund, and nothing here can pay money out.
    """
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status in {InvoiceStatus.CANCELLED, InvoiceStatus.WAIVED}:
        raise FinanceError(
            "A cancelled or waived bill cannot be adjusted.", code="INVOICE_NOT_ADJUSTABLE"
        )

    before = {
        "subtotal": str(invoice.subtotal),
        "discount": str(invoice.discount),
        "total": str(invoice.total),
        "lines": [
            {"description": line.description, "amount": str(line.amount)}
            for line in invoice.lines.all()
        ],
    }

    invoice.lines.all().delete()
    InvoiceLine.objects.bulk_create(InvoiceLine(invoice=invoice, **line) for line in lines)
    if discount is not None:
        invoice.discount = Decimal(discount)
    invoice.recompute_totals()

    if invoice.total < invoice.amount_paid:
        # Raised inside the transaction, so the lines just deleted come back.
        raise FinanceError(
            f"{invoice.amount_paid} has already been paid; a bill cannot be adjusted below that.",
            code="ADJUSTMENT_BELOW_PAID",
            details={"amount_paid": str(invoice.amount_paid), "proposed_total": str(invoice.total)},
        )

    # The status follows the new total: a bill adjusted down to what is already
    # paid is PAID, and one adjusted up from PAID is PART_PAID and payable again.
    invoice.recompute_paid()
    invoice.save(
        update_fields=["discount", "subtotal", "total", "amount_paid", "status", "updated_at"]
    )
    record(
        AuditAction.INVOICE_ADJUSTED,
        request=request,
        actor=actor,
        obj=invoice,
        before=before,
        after={
            "subtotal": str(invoice.subtotal),
            "discount": str(invoice.discount),
            "total": str(invoice.total),
            "reason": reason,
        },
        summary=f"{invoice.number}: {reason}"[:255],
    )
    return invoice


# ---------------------------------------------------------------------------
# Raising invoices
# ---------------------------------------------------------------------------
def cohort_for(structure) -> list[Enrolment]:
    """Who this structure bills: active enrolments in its session and scope."""
    queryset = Enrolment.objects.filter(
        session=structure.session, status=EnrolmentStatus.ACTIVE
    ).select_related("student")
    if structure.level_id:
        queryset = queryset.filter(level_id=structure.level_id)
    if structure.programme_id:
        queryset = queryset.filter(programme_id=structure.programme_id)
    return list(queryset)


def generate_invoices(
    *, structure, student_ids: list[str] | None = None, actor=None
) -> BulkOutcome:
    """One invoice per student in the cohort. Safe to run again.

    Existing invoices are left exactly as they are — a top-up run must not
    re-price a bill a parent has already part-paid — so a second run reports
    only what it newly created.
    """
    items = list(structure.items.filter(is_optional=False))
    if not items:
        raise NothingToBill()

    enrolments = cohort_for(structure)
    if student_ids is not None:
        wanted = {str(pk) for pk in student_ids}
        enrolments = [e for e in enrolments if str(e.student_id) in wanted]

    def handler(outcome: BulkOutcome) -> None:
        already = set(
            Invoice.objects.alive().filter(structure=structure).values_list("student_id", flat=True)
        )
        year = timezone.now().year
        for index, enrolment in enumerate(enrolments):
            if enrolment.student_id in already:
                continue
            if enrolment.student.deleted_at is not None:
                outcome.errors.append(
                    RowError(index, str(enrolment.student_id), "STUDENT_DELETED", "Record removed.")
                )
                continue
            invoice = Invoice.objects.create(
                number=next_number(
                    Invoice, "number", INVOICE_NUMBER_FORMAT, NumberContext(year=year)
                ),
                student=enrolment.student,
                session=structure.session,
                term=structure.term,
                structure=structure,
                status=InvoiceStatus.DRAFT,
                due_date=timezone.localdate() + timedelta(days=structure.due_in_days),
            )
            InvoiceLine.objects.bulk_create(
                InvoiceLine(
                    invoice=invoice,
                    description=item.name,
                    category=item.category,
                    amount=item.amount,
                    fee_item=item,
                )
                for item in items
            )
            invoice.recompute_totals()
            invoice.save(update_fields=["subtotal", "total", "updated_at"])
            outcome.created += 1

    outcome = atomic_bulk(handler)
    logger.info(
        "invoices generated",
        # Not `created`: that is a reserved LogRecord attribute and setting it
        # raises rather than logging.
        extra={"structure": str(structure.pk), "invoices_created": outcome.created},
    )
    return outcome


@tenant_atomic()
def issue_invoice(invoice: Invoice, *, actor=None, request=None) -> Invoice:
    """Draft → issued. This is what makes a bill visible to a parent."""
    if invoice.status != InvoiceStatus.DRAFT:
        return invoice
    invoice.recompute_totals()
    invoice.issued_at = timezone.now()
    invoice.status = InvoiceStatus.ISSUED
    invoice.due_date = invoice.due_date or timezone.localdate() + timedelta(days=21)
    invoice.save(
        update_fields=["subtotal", "total", "issued_at", "status", "due_date", "updated_at"]
    )
    record(
        AuditAction.INVOICE_ISSUED,
        request=request,
        actor=actor,
        obj=invoice,
        summary=f"{invoice.number} — {invoice.total}",
    )
    return invoice


@tenant_atomic()
def waive_invoice(invoice: Invoice, *, reason: str, actor=None, request=None) -> Invoice:
    """Write a bill off. Kept, never deleted — a waiver is a decision."""
    invoice.status = InvoiceStatus.WAIVED
    invoice.waiver_reason = reason
    invoice.save(update_fields=["status", "waiver_reason", "updated_at"])
    record(
        AuditAction.INVOICE_WAIVED,
        request=request,
        actor=actor,
        obj=invoice,
        summary=f"{invoice.number}: {reason}"[:255],
    )
    return invoice


@tenant_atomic()
def cancel_invoice(invoice: Invoice, *, reason: str = "", actor=None, request=None) -> Invoice:
    if invoice.payments.filter(status=PaymentStatus.SUCCESS).exists():
        raise FinanceError(
            "Reverse the payments before cancelling this invoice.", code="INVOICE_HAS_PAYMENTS"
        )
    invoice.status = InvoiceStatus.CANCELLED
    invoice.note = (reason or invoice.note)[:255]
    invoice.save(update_fields=["status", "note", "updated_at"])
    record(
        AuditAction.INVOICE_CANCELLED,
        request=request,
        actor=actor,
        obj=invoice,
        summary=f"{invoice.number}: {reason}"[:255],
    )
    return invoice


# ---------------------------------------------------------------------------
# Taking money
# ---------------------------------------------------------------------------
def _receipt_number() -> str:
    return next_number(
        Payment, "reference", RECEIPT_NUMBER_FORMAT, NumberContext(year=timezone.now().year)
    )


def _assert_payable(invoice: Invoice, amount: Decimal) -> None:
    if not invoice.is_payable:
        raise InvoiceNotPayable()
    if amount <= ZERO or amount > invoice.balance:
        raise PaymentAmountInvalid(
            f"The outstanding balance is {invoice.balance}.",
            details={"balance": str(invoice.balance)},
        )


@tenant_atomic()
def record_payment(
    *,
    invoice: Invoice,
    amount: Decimal,
    method: str = PaymentMethod.CASH,
    payer_name: str = "",
    payer_phone: str = "",
    note: str = "",
    recorded_by=None,
    actor=None,
    request=None,
) -> Payment:
    """Cash, transfer or POS taken at the counter. Successful on arrival."""
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    _assert_payable(invoice, amount)

    payment = Payment.objects.create(
        invoice=invoice,
        reference=_receipt_number(),
        amount=amount,
        method=method,
        status=PaymentStatus.SUCCESS,
        paid_at=timezone.now(),
        recorded_by=recorded_by,
        payer_name=payer_name,
        payer_phone=payer_phone,
        note=note,
    )
    invoice.recompute_paid()
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])
    record(
        AuditAction.PAYMENT_RECORDED,
        request=request,
        actor=actor,
        obj=payment,
        summary=f"{payment.reference} {amount} on {invoice.number}",
    )
    return payment


@tenant_atomic()
def start_online_payment(
    *,
    invoice: Invoice,
    gateway_name: str,
    tenant=None,
    amount: Decimal | None = None,
    email: str = "",
    callback_url: str = "",
    payer_phone: str = "",
):
    """Open a checkout and park a PENDING payment against its reference.

    The row is written *before* the gateway is called, so a webhook that beats
    the HTTP response back to us still finds something to confirm.

    ponytail: no lock on the invoice. Two checkouts opened at once both quote
    the full balance, and if a parent genuinely pays both, the invoice ends up
    over-paid — which is what happened, and is visible as `amount_paid` above
    `total`. Locking the invoice for the length of an HTTP call to Paystack
    would trade a rare refund for a common timeout.
    """
    amount = Decimal(amount) if amount is not None else invoice.balance
    _assert_payable(invoice, amount)

    gateway = get_gateway(gateway_name, tenant)
    payment = Payment.objects.create(
        invoice=invoice,
        reference=_receipt_number(),
        amount=amount,
        method=PaymentMethod.ONLINE,
        status=PaymentStatus.PENDING,
        gateway=gateway.name,
        payer_phone=payer_phone,
    )
    charge = gateway.initialize(
        reference=payment.reference,
        amount=amount,
        email=email,
        callback_url=callback_url,
        invoice=invoice.number,
    )
    if not charge.ok:
        payment.status = PaymentStatus.FAILED
        payment.note = charge.error[:255]
        payment.save(update_fields=["status", "note", "updated_at"])
        raise FinanceError(
            charge.error or "The payment gateway could not open a checkout.",
            code="GATEWAY_UNAVAILABLE",
        )
    return payment, charge


@tenant_atomic()
def confirm_payment(
    verification: Verification, *, gateway_name: str = "", request=None
) -> Payment | None:
    """Apply a gateway verdict. The one path to a successful online payment.

    Returns the payment, or None when the reference is not ours — which a
    webhook handler answers with 200 anyway, because a gateway that is told
    "unknown" simply retries the same unknown thing for a day.

    Idempotent three times over: an already-successful payment returns
    untouched, the amount credited is the amount the gateway says arrived (not
    the amount we asked for), and `gateway_reference` is unique, so two
    webhooks for one charge cannot become two payments.
    """
    payment = (
        Payment.objects.select_for_update()
        .filter(reference=verification.reference)
        .select_related("invoice")
        .first()
    )
    if payment is None:
        logger.warning(
            "payment webhook for an unknown reference",
            extra={"reference": verification.reference, "gateway": gateway_name},
        )
        return None

    if payment.status == PaymentStatus.SUCCESS:
        return payment  # already credited; a replayed webhook changes nothing

    # A reversal is a decision, and this is the one path that could undo it
    # silently. Gateways retry a webhook for a day, `sweep_pending` re-verifies,
    # and `POST /payments/{id}/verify/` is callable on any payment by anybody
    # holding `IsAuthenticated` — so a bounced settlement reversed by the bursar
    # on Monday was taken back to SUCCESS by Tuesday's replay, `recompute_paid`
    # credited the money a second time, and the row was left reading SUCCESS
    # with a `reversal_reason` still on it. FAILED *is* still rescuable (see
    # `sweep_pending`): an abandoned checkout that really did settle is a
    # payment we want. REVERSED is not — somebody looked at it and said no.
    if payment.status == PaymentStatus.REVERSED:
        logger.warning(
            "gateway reported a payment that was already reversed",
            extra={
                "reference": payment.reference,
                "gateway": payment.gateway or gateway_name,
                "reversal_reason": payment.reversal_reason,
            },
        )
        return payment

    payment.gateway = payment.gateway or gateway_name
    # NULL, not "", when the gateway named nothing: the unique constraint is
    # unconditional, so a second empty string would collide with the first.
    payment.gateway_reference = verification.gateway_reference or None
    payment.raw = verification.raw

    if not verification.ok:
        payment.status = PaymentStatus.FAILED
        payment.note = (verification.error or verification.status)[:255]
        payment.save(
            update_fields=["status", "note", "gateway", "gateway_reference", "raw", "updated_at"]
        )
        return payment

    # Never credit more than was asked for. The amount is fixed when the
    # checkout is opened, so a gateway reporting *more* than that is not
    # something a payer can produce — but an inflated webhook body is, and
    # Flutterwave signs no body at all (`verif-hash` is a static shared
    # secret), so nothing upstream can rule it out. Crediting it marks the
    # invoice PAID for money nobody sent. Both `gateways/base.py` and
    # `gateways/flutterwave.py` already promised this check in their
    # docstrings; it was never written.
    #
    # Left PENDING rather than FAILED: the charge may well be real. A bursar
    # clicking verify reads the gateway's own API instead of a posted body and
    # comes back through here, and `sweep_pending` retries it meanwhile.
    if verification.amount > payment.amount:
        payment.note = (
            f"Requested {payment.amount}, reported {verification.amount}; not credited."
        )[:255]
        payment.save(update_fields=["note", "gateway", "gateway_reference", "raw", "updated_at"])
        logger.error(
            "gateway reported more than the checkout was opened for",
            extra={
                "reference": payment.reference,
                "gateway": payment.gateway,
                "requested": str(payment.amount),
                "reported": str(verification.amount),
            },
        )
        return payment

    # Credit what actually arrived. A parent who pays a fee-inclusive total,
    # or a gateway that settles net of its charge, must leave the invoice
    # showing the true figure rather than the one we asked for.
    if verification.amount > ZERO and verification.amount != payment.amount:
        payment.note = f"Requested {payment.amount}, settled {verification.amount}."
        payment.amount = verification.amount
    payment.status = PaymentStatus.SUCCESS
    payment.paid_at = timezone.now()
    payment.save()

    invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    invoice.recompute_paid()
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])
    payment.invoice = invoice

    record(
        AuditAction.PAYMENT_CONFIRMED,
        request=request,
        obj=payment,
        summary=f"{payment.reference} {payment.amount} via {payment.gateway}",
    )
    return payment


def verify_payment(payment: Payment, *, tenant=None, request=None) -> Payment:
    """Ask the gateway directly. The fallback for a webhook that never came."""
    gateway = get_gateway(payment.gateway or "manual", tenant)
    confirmed = confirm_payment(
        gateway.verify(payment.reference), gateway_name=gateway.name, request=request
    )
    return confirmed or payment


@tenant_atomic()
def reverse_payment(payment: Payment, *, reason: str, actor=None, request=None) -> Payment:
    """Bounced cheque, failed settlement, receipt keyed against the wrong bill."""
    if payment.status != PaymentStatus.SUCCESS:
        raise FinanceError("Only a successful payment can be reversed.", code="NOT_REVERSIBLE")
    payment.status = PaymentStatus.REVERSED
    payment.reversal_reason = reason
    payment.save(update_fields=["status", "reversal_reason", "updated_at"])

    invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    invoice.recompute_paid()
    invoice.save(update_fields=["amount_paid", "status", "updated_at"])

    record(
        AuditAction.PAYMENT_REVERSED,
        request=request,
        actor=actor,
        obj=payment,
        summary=f"{payment.reference}: {reason}"[:255],
    )
    return payment


#: How long a checkout nobody completed keeps being asked about. Most PENDING
#: rows are abandoned carts — a parent opened Paystack and closed the tab — and
#: without a floor the sweep asks the gateway about every one of them, every
#: thirty minutes, for the life of the school. Two days is well past any
#: settlement delay a Nigerian gateway has and well short of forever.
ABANDON_AFTER_DAYS = 2


def sweep_pending(
    *, older_than_minutes: int = 20, abandon_after_days: int = ABANDON_AFTER_DAYS, tenant=None
) -> dict:
    """Re-verify online payments the webhook never reported on, then give up.

    Giving up is the half that was missing. Nothing else in the system ever
    resolved a PENDING row, so the query grew monotonically and the beat task
    spent an ever-larger share of its gateway rate limit re-asking about
    checkouts that were abandoned months ago.

    Abandoning is not final: `confirm_payment` will still take a FAILED row to
    SUCCESS, so a webhook that turns up late, or a bursar clicking verify, can
    still rescue a payment that really did happen.

    ponytail: a loop over the pending rows, called from a Celery beat task or
    by a bursar. It is a handful of rows a day; a queue per payment would be
    machinery for a problem nobody has.
    """
    now = timezone.now()
    fresh_enough = now - timedelta(minutes=older_than_minutes)
    too_old = now - timedelta(days=abandon_after_days)

    open_checkouts = Payment.objects.filter(
        status=PaymentStatus.PENDING, method=PaymentMethod.ONLINE
    )
    abandoned = open_checkouts.filter(created_at__lt=too_old).update(
        status=PaymentStatus.FAILED,
        note=f"No confirmation within {abandon_after_days} days; treated as an abandoned checkout.",
        updated_at=now,
    )

    pending = list(open_checkouts.filter(created_at__lt=fresh_enough, created_at__gte=too_old))
    settled = 0
    for payment in pending:
        try:
            if verify_payment(payment, tenant=tenant).status == PaymentStatus.SUCCESS:
                settled += 1
        except Exception:  # a gateway being down must not stop the sweep
            logger.exception("sweep failed for a payment", extra={"payment": str(payment.pk)})
    return {"checked": len(pending), "settled": settled, "abandoned": abandoned}
