"""Editing fees after they exist.

Two edits, two different rules. Re-pricing the *list* reaches the bills nobody
has issued and stops at the ones a parent is holding. Adjusting one *bill*
rewrites its lines, and refuses to go below the money already taken.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.audit.models import AuditAction, AuditLog
from apps.finance import services
from apps.finance.models import FeeItem, FeeStructure, Invoice, InvoiceStatus
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("queens-college")


@pytest.fixture
def billed(school):
    """One draft invoice for one student, off a one-item structure."""
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
            is_current=True,
        )
        level = ClassLevel.objects.get(code="JSS1")
        student = Student.objects.create(
            admission_number="QC/25/0001",
            first_name="Aisha",
            last_name="Bello",
            current_level=level,
        )
        enrol_student(student=student, session=session, level=level)
        structure = FeeStructure.objects.create(
            name="First Term", session=session, term=term, level=level
        )
        item = FeeItem.objects.create(
            structure=structure, name="Tuition", amount=Decimal("50000.00")
        )
        services.generate_invoices(structure=structure)
        yield item, Invoice.objects.get()


def test_repricing_an_item_reaches_the_drafts(school, billed):
    item, invoice = billed
    with schema_context(school.schema_name):
        item.amount = Decimal("60000.00")
        item.save()

        assert services.fee_changed(item) == 1

        invoice.refresh_from_db()
        assert invoice.total == Decimal("60000.00")
        assert invoice.lines.get().amount == Decimal("60000.00")
        assert AuditLog.objects.filter(action=AuditAction.FEE_CHANGED).exists()


def test_repricing_leaves_an_issued_bill_alone(school, billed):
    """The snapshot rule: a bill a parent is holding does not move."""
    item, invoice = billed
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)

        item.amount = Decimal("60000.00")
        item.save()
        assert services.fee_changed(item) == 0

        invoice.refresh_from_db()
        assert invoice.total == Decimal("50000.00")


def test_adjusting_a_bill_rewrites_its_lines_and_total(school, billed):
    _, invoice = billed
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)

        services.adjust_invoice(
            invoice,
            lines=[
                {"description": "Tuition", "category": "TUITION", "amount": Decimal("50000.00")},
                {"description": "Sibling rebate", "amount": Decimal("0.00")},
            ],
            discount=Decimal("5000.00"),
            reason="Second child in the school.",
        )

        invoice.refresh_from_db()
        assert invoice.lines.count() == 2
        assert invoice.subtotal == Decimal("50000.00")
        assert invoice.total == Decimal("45000.00")
        assert AuditLog.objects.filter(action=AuditAction.INVOICE_ADJUSTED).exists()


def test_an_adjustment_below_what_was_paid_is_refused_and_changes_nothing(school, billed):
    _, invoice = billed
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        services.record_payment(invoice=invoice, amount=Decimal("40000.00"))

        with pytest.raises(services.FinanceError) as excinfo:
            services.adjust_invoice(
                invoice,
                lines=[{"description": "Tuition", "amount": Decimal("30000.00")}],
                reason="Typo.",
            )
        assert excinfo.value.code == "ADJUSTMENT_BELOW_PAID"

        invoice.refresh_from_db()
        assert invoice.total == Decimal("50000.00")
        assert invoice.lines.count() == 1  # the delete rolled back with it


def test_an_adjustment_down_to_what_was_paid_settles_the_bill(school, billed):
    _, invoice = billed
    with schema_context(school.schema_name):
        services.issue_invoice(invoice)
        services.record_payment(invoice=invoice, amount=Decimal("40000.00"))

        services.adjust_invoice(
            invoice,
            lines=[{"description": "Tuition", "amount": Decimal("40000.00")}],
            reason="Boarding charged in error.",
        )

        invoice.refresh_from_db()
        assert invoice.total == Decimal("40000.00")
        assert invoice.balance == Decimal("0.00")
        assert invoice.status == InvoiceStatus.PAID


def test_a_waived_bill_cannot_be_adjusted(school, billed):
    _, invoice = billed
    with schema_context(school.schema_name):
        services.waive_invoice(invoice, reason="Staff child.")

        with pytest.raises(services.FinanceError) as excinfo:
            services.adjust_invoice(
                invoice,
                lines=[{"description": "Tuition", "amount": Decimal("1.00")}],
                reason="No.",
            )
        assert excinfo.value.code == "INVOICE_NOT_ADJUSTABLE"
