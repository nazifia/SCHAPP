"""Online payments: the checkout, the webhook, and the ways both go wrong.

The gateway is the `manual` adapter, so this exercises the real reference,
the real PENDING row and the real `confirm_payment` — everything except the
HTTP call to Paystack.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.finance import services
from apps.finance.gateways import Verification, get_gateway
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
            admission_number="KC/25/0001",
            first_name="Ngozi",
            last_name="Ali",
            current_level=level,
        )
        enrol_student(student=student, session=session, level=level)

        structure = FeeStructure.objects.create(
            name="JSS1 First Term", session=session, term=term, level=level
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))
        services.generate_invoices(structure=structure)
        yield services.issue_invoice(Invoice.objects.get())


def test_checkout_parks_a_pending_payment_before_the_gateway_is_called(school, invoice):
    with schema_context(school.schema_name):
        payment, charge = services.start_online_payment(
            invoice=invoice, gateway_name="manual", email="parent@example.ng"
        )
        assert charge.ok
        assert payment.status == PaymentStatus.PENDING
        assert payment.reference in charge.checkout_url
        # Nothing is credited until the gateway says so.
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0.00")


def test_a_webhook_settles_the_invoice_and_a_replay_changes_nothing(school, invoice):
    with schema_context(school.schema_name):
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        gateway = get_gateway("manual")

        confirmed = services.confirm_payment(
            gateway.verify(payment.reference), gateway_name="manual"
        )
        assert confirmed.status == PaymentStatus.SUCCESS
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID

        # The same webhook again: same row, same balance.
        services.confirm_payment(gateway.verify(payment.reference), gateway_name="manual")
        invoice.refresh_from_db()
        assert Payment.objects.count() == 1
        assert invoice.amount_paid == Decimal("50000.00")


def test_a_webhook_for_an_unknown_reference_is_ignored(school, invoice):
    with schema_context(school.schema_name):
        result = services.confirm_payment(
            Verification(ok=True, reference="RCT/99/9999", amount=Decimal("1.00")),
            gateway_name="manual",
        )
        assert result is None
        assert not Payment.objects.exists()


def test_the_amount_credited_is_the_amount_the_gateway_reports(school, invoice):
    """A parent who edits the amount at checkout leaves a true balance behind."""
    with schema_context(school.schema_name):
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        services.confirm_payment(
            Verification(
                ok=True,
                reference=payment.reference,
                gateway_reference="gw-1",
                amount=Decimal("20000.00"),
            ),
            gateway_name="manual",
        )
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("20000.00")
        assert invoice.status == InvoiceStatus.PART_PAID


def test_a_failed_charge_leaves_the_invoice_untouched(school, invoice):
    with schema_context(school.schema_name):
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        services.confirm_payment(
            Verification(ok=False, reference=payment.reference, status="abandoned"),
            gateway_name="manual",
        )
        payment.refresh_from_db()
        invoice.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.status == InvoiceStatus.ISSUED


def test_verify_settles_a_payment_whose_webhook_never_arrived(school, invoice):
    with schema_context(school.schema_name):
        payment, _ = services.start_online_payment(invoice=invoice, gateway_name="manual")
        settled = services.verify_payment(payment)
        assert settled.status == PaymentStatus.SUCCESS
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
