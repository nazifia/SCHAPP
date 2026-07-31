"""Two ways the money layer never let go.

* `sweep_pending` re-asked the gateway about every PENDING row it had ever
  written. Most of them are abandoned carts — a parent opened Paystack and
  closed the tab — and nothing else in the system ever resolved one, so the
  beat task's work grew monotonically for the life of the school.
* `Invoice.recompute_paid` had no `else`. A bill paid before it was issued
  goes to PAID; reversing that payment then left it PAID with the whole
  balance outstanding, and PAID is not payable — the invoice was stuck.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.finance import services
from apps.finance.models import (
    FeeItem,
    FeeStructure,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentStatus,
)
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def invoice(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        term = Term.objects.create(
            session=session,
            index=1,
            name="First Term",
            start_date="2025-09-01",
            end_date="2025-12-15",
        )
        level = ClassLevel.objects.get(code="JSS1")
        student = Student.objects.create(
            admission_number="KC/25/0001", first_name="Ngozi", last_name="Ali", current_level=level
        )
        enrol_student(student=student, session=session, level=level)
        structure = FeeStructure.objects.create(
            name="JSS1 First Term", session=session, term=term, level=level
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))
        services.generate_invoices(structure=structure)
        yield Invoice.objects.get()


def _age(payment, days):
    """Backdate a row. `created_at` is auto_now_add, so it takes an UPDATE."""
    Payment.objects.filter(pk=payment.pk).update(
        created_at=timezone.now() - timedelta(days=days, minutes=1)
    )


# ---------------------------------------------------------------------------
# The sweep gives up
# ---------------------------------------------------------------------------
def test_an_abandoned_checkout_stops_being_swept(school, invoice):
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        _age(payment, services.ABANDON_AFTER_DAYS)

        result = services.sweep_pending()
        assert result["abandoned"] == 1
        # Not verified this run, and not a candidate for any future run either.
        assert result["checked"] == 0
        assert services.sweep_pending()["checked"] == 0

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert "abandoned checkout" in payment.note


def test_a_recent_checkout_is_still_verified(school, invoice):
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        _age(payment, 1)  # inside the window: old enough to check, not to abandon

        result = services.sweep_pending()
        assert result == {"checked": 1, "settled": 1, "abandoned": 0}
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.SUCCESS


def test_a_checkout_minutes_old_is_left_alone(school, invoice):
    """The parent may still be on the gateway's page."""
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        services.start_online_payment(invoice=invoice, gateway_name="manual")
        assert services.sweep_pending() == {"checked": 0, "settled": 0, "abandoned": 0}


def test_abandoning_is_not_final(school, invoice):
    """A parent who really did pay must still be credited when the news
    finally arrives — abandoning stops the polling, it does not close the
    case."""
    from apps.finance.gateways import get_gateway

    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        _age(payment, services.ABANDON_AFTER_DAYS)
        services.sweep_pending()

        confirmed = services.confirm_payment(
            get_gateway("manual").verify(payment.reference), gateway_name="manual"
        )
        assert confirmed.status == PaymentStatus.SUCCESS
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID


# ---------------------------------------------------------------------------
# A reversal returns the invoice to a payable state
# ---------------------------------------------------------------------------
def test_reversing_a_payment_on_an_unissued_invoice_leaves_it_payable(school, invoice):
    """Counter payments are accepted on a DRAFT bill, so this is reachable:
    money in, money back out, and the invoice must not be left declaring
    itself PAID while the whole balance is outstanding."""
    with schema_context(school.schema_name):
        assert invoice.status == InvoiceStatus.DRAFT
        payment = services.record_payment(invoice=invoice, amount=Decimal("50000.00"))
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID

        services.reverse_payment(payment, reason="Cheque bounced.")
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.status == InvoiceStatus.DRAFT
        assert invoice.is_payable  # the bursar can take the money again

        services.record_payment(invoice=invoice, amount=Decimal("50000.00"))
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID


def test_reversal_on_an_issued_invoice_goes_back_to_issued(school, invoice):
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment = services.record_payment(invoice=invoice, amount=Decimal("50000.00"))
        services.reverse_payment(payment, reason="Keyed against the wrong bill.")
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.ISSUED
        assert invoice.is_payable


def test_a_waived_invoice_is_not_dragged_back_by_a_reversal(school, invoice):
    """`recompute_paid` returns early for WAIVED and CANCELLED; the new `else`
    must not have opened a way past that."""
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment = services.record_payment(invoice=invoice, amount=Decimal("10000.00"))
        services.waive_invoice(invoice, reason="Hardship.")
        services.reverse_payment(payment, reason="Posted twice.")
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.WAIVED


def test_a_reversed_payment_is_not_resurrected_by_a_replayed_webhook(school, invoice):
    """The counterpart of `test_abandoning_is_not_final`, and its opposite.

    FAILED is rescuable; REVERSED is a decision. A gateway retries its webhook
    for a day and `POST /payments/{id}/verify/` is callable on any payment, so
    without the guard the bursar's Monday reversal was undone by Tuesday's
    replay — credited a second time, and left reading SUCCESS with a
    `reversal_reason` still on the row.
    """
    from apps.finance.gateways import get_gateway

    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        services.confirm_payment(
            get_gateway("manual").verify(payment.reference), gateway_name="manual"
        )
        payment.refresh_from_db()
        services.reverse_payment(payment, reason="Settlement clawed back.")

        again = services.confirm_payment(
            get_gateway("manual").verify(payment.reference), gateway_name="manual"
        )
        assert again.status == PaymentStatus.REVERSED

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.REVERSED
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.status == InvoiceStatus.ISSUED

        # A bursar clicking verify goes through the same one path.
        assert services.verify_payment(payment).status == PaymentStatus.REVERSED
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0.00")
