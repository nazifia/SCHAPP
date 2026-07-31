"""A discount has to reach the total, or it is a number on a screen.

`discount` is the one writable field the money depends on, and the only thing
that reads it — `recompute_totals` — ran in two places a PATCH never reaches.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.finance import services
from apps.finance.models import FeeItem, FeeStructure, Invoice, InvoiceStatus
from apps.finance.serializers import InvoiceSerializer
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
            is_current=True,
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
            name="First Term", session=session, term=term, level=level
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("50000.00"))
        services.generate_invoices(structure=structure)
        yield services.issue_invoice(Invoice.objects.get())


def _discount(invoice, amount: str) -> Invoice:
    serializer = InvoiceSerializer(invoice, data={"discount": amount}, partial=True)
    serializer.is_valid(raise_exception=True)
    return serializer.save()


def test_discounting_an_issued_invoice_changes_what_is_owed(school, invoice):
    with schema_context(school.schema_name):
        assert invoice.total == Decimal("50000.00")

        _discount(invoice, "12000.00")

        invoice.refresh_from_db()
        assert invoice.discount == Decimal("12000.00")
        assert invoice.total == Decimal("38000.00")
        assert invoice.balance == Decimal("38000.00")


def test_a_discount_down_to_what_is_already_paid_settles_the_bill(school, invoice):
    """The status half. Without it the bill stays PART_PAID with a negative
    balance, which `_assert_payable` then refuses every further payment against
    — an invoice nobody can pay and nothing can close."""
    with schema_context(school.schema_name):
        services.record_payment(invoice=invoice, amount=Decimal("30000.00"))
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PART_PAID

        _discount(invoice, "20000.00")

        invoice.refresh_from_db()
        assert invoice.total == Decimal("30000.00")
        assert invoice.balance == Decimal("0.00")
        assert invoice.status == InvoiceStatus.PAID


def test_an_edit_that_does_not_touch_the_discount_leaves_the_total_alone(school, invoice):
    with schema_context(school.schema_name):
        serializer = InvoiceSerializer(invoice, data={"note": "Paid at the gate."}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        invoice.refresh_from_db()
        assert invoice.note == "Paid at the gate."
        assert invoice.total == Decimal("50000.00")
