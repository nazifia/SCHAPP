"""Finance reads: who may see which bill, and what the term collected.

The scoping rule is narrower than the rest of the platform on purpose. A
teacher can see a pupil's marks and cannot see the pupil's fees — school fees
are the parent's business and the bursar's, and a staffroom is not private.
So visibility here is not "students I can see": it is `finance.view_invoice`
(the bursar's office) or `self.view_own_invoice` (the payer).
"""

from decimal import Decimal

from django.db.models import Count, F, Q, QuerySet, Sum

from apps.people.selectors import students_visible_to

from .models import ZERO, Invoice, InvoiceLine, InvoiceStatus, Payment, PaymentStatus

#: Bills that still represent money owed.
UNSETTLED = {InvoiceStatus.ISSUED, InvoiceStatus.PART_PAID}


def invoices_visible_to(user) -> QuerySet[Invoice]:
    base = Invoice.objects.alive().select_related("student", "term", "session")
    if not (user and user.is_authenticated):
        return base.none()
    if user.has_perm_code("finance.view_invoice"):
        return base
    if user.has_perm_code("self.view_own_invoice"):
        # `students_visible_to` already narrows a guardian to their wards and a
        # student to themselves; the permission above is what admits them here.
        return base.filter(student__in=students_visible_to(user)).exclude(
            status=InvoiceStatus.DRAFT
        )
    return base.none()


def payments_visible_to(user) -> QuerySet[Payment]:
    return Payment.objects.filter(invoice__in=invoices_visible_to(user)).select_related(
        "invoice", "invoice__student"
    )


def statement(student) -> dict:
    """Every bill and every receipt for one student, oldest first.

    What a parent asks for at the gate: "how much have I paid and how much is
    left". One query each, no per-invoice follow-ups.
    """
    invoices = (
        Invoice.objects.alive()
        .filter(student=student)
        .exclude(status=InvoiceStatus.DRAFT)
        .select_related("term", "session")
        .prefetch_related("lines", "payments")
        .order_by("created_at")
    )
    billed = sum((invoice.total for invoice in invoices), ZERO)
    paid = sum((invoice.amount_paid for invoice in invoices), ZERO)
    return {
        "billed": billed,
        "paid": paid,
        "balance": billed - paid,
        "invoices": [
            {
                "id": str(invoice.pk),
                "number": invoice.number,
                "term": invoice.term.name if invoice.term else invoice.session.name,
                "status": invoice.status,
                "total": invoice.total,
                "amount_paid": invoice.amount_paid,
                "balance": invoice.balance,
                "due_date": invoice.due_date,
                "lines": [
                    {"description": line.description, "amount": line.amount}
                    for line in invoice.lines.all()
                ],
                "payments": [
                    {
                        "reference": payment.reference,
                        "amount": payment.amount,
                        "method": payment.method,
                        "status": payment.status,
                        "paid_at": payment.paid_at,
                    }
                    for payment in invoice.payments.all()
                    if payment.status != PaymentStatus.PENDING
                ],
            }
            for invoice in invoices
        ],
    }


def collection_summary(*, session=None, term=None) -> dict:
    """Billed / collected / outstanding, plus the split by fee category.

    The three numbers a proprietor asks for, and the fourth question they ask
    straight afterwards — *which* fee is not coming in.
    """
    invoices = Invoice.objects.alive().exclude(
        status__in=[InvoiceStatus.DRAFT, InvoiceStatus.CANCELLED]
    )
    if session is not None:
        invoices = invoices.filter(session=session)
    if term is not None:
        invoices = invoices.filter(term=term)

    totals = invoices.aggregate(
        billed=Sum("total"),
        collected=Sum("amount_paid"),
        invoices=Count("id"),
        settled=Count("id", filter=Q(status=InvoiceStatus.PAID)),
    )
    billed = totals["billed"] or ZERO
    collected = totals["collected"] or ZERO

    by_category = (
        InvoiceLine.objects.filter(invoice__in=invoices)
        .values("category")
        .annotate(billed=Sum("amount"))
        .order_by("-billed")
    )
    return {
        "billed": billed,
        "collected": collected,
        "outstanding": billed - collected,
        "collection_rate": _rate(collected, billed),
        "invoices": totals["invoices"] or 0,
        "settled": totals["settled"] or 0,
        "by_category": list(by_category),
    }


def defaulters(*, session=None, term=None, minimum: Decimal = ZERO) -> list[dict]:
    """Unsettled bills past their due date, biggest balance first."""
    from django.utils import timezone

    queryset = (
        Invoice.objects.alive()
        .filter(status__in=UNSETTLED, due_date__lt=timezone.localdate())
        .annotate(outstanding=F("total") - F("amount_paid"))
        .filter(outstanding__gt=minimum)
        .select_related("student", "term")
        .order_by("-outstanding")
    )
    if session is not None:
        queryset = queryset.filter(session=session)
    if term is not None:
        queryset = queryset.filter(term=term)

    return [
        {
            "invoice": str(invoice.pk),
            "number": invoice.number,
            "student": str(invoice.student_id),
            "full_name": invoice.student.full_name,
            "admission_number": invoice.student.display_number,
            "term": invoice.term.name if invoice.term else "",
            "total": invoice.total,
            "amount_paid": invoice.amount_paid,
            "outstanding": invoice.outstanding,
            "due_date": invoice.due_date,
            "days_overdue": (timezone.localdate() - invoice.due_date).days,
        }
        for invoice in queryset
    ]


def _rate(part: Decimal, whole: Decimal) -> Decimal:
    if not whole:
        return ZERO
    return (part / whole * 100).quantize(Decimal("0.01"))
