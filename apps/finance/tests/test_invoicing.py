"""Raising bills and taking money at the counter.

The properties worth a test are the ones a bursar would notice going wrong:
a second generation run must not bill anybody twice, a payment must never take
an invoice past its total, and a reversal must put the balance back.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.finance import selectors, services
from apps.finance.models import (
    FeeItem,
    FeeStructure,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
    PaymentStatus,
)
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def setup(school):
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
        arm = ClassArm.objects.create(level=level, name="A")

        students = []
        for index, (first, last) in enumerate(
            [("Ngozi", "Ali"), ("Chidi", "Eze"), ("Bola", "Ade")], start=1
        ):
            student = Student.objects.create(
                admission_number=f"KC/25/000{index}",
                first_name=first,
                last_name=last,
                current_level=level,
                current_arm=arm,
            )
            enrol_student(student=student, session=session, level=level, class_arm=arm)
            students.append(student)

        structure = FeeStructure.objects.create(
            name="JSS1 First Term", session=session, term=term, level=level
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("80000.00"))
        FeeItem.objects.create(structure=structure, name="Books", amount=Decimal("20000.00"))
        FeeItem.objects.create(
            structure=structure, name="Bus", amount=Decimal("30000.00"), is_optional=True
        )

        yield {
            "session": session,
            "term": term,
            "level": level,
            "arm": arm,
            "structure": structure,
            "students": students,
        }


def test_generation_bills_the_cohort_once_per_structure(school, setup):
    with schema_context(school.schema_name):
        outcome = services.generate_invoices(structure=setup["structure"])
        assert outcome.ok
        assert outcome.created == 3
        assert Invoice.objects.count() == 3

        invoice = Invoice.objects.first()
        # Optional items are opt-in, so the bus fare is not on the bill.
        assert invoice.total == Decimal("100000.00")
        assert invoice.lines.count() == 2

        # A second run after a new admission bills only the new student.
        student = Student.objects.create(
            admission_number="KC/25/0004",
            first_name="Tunde",
            last_name="Bakare",
            current_level=setup["level"],
            current_arm=setup["arm"],
        )
        enrol_student(
            student=student,
            session=setup["session"],
            level=setup["level"],
            class_arm=setup["arm"],
        )
        again = services.generate_invoices(structure=setup["structure"])
        assert again.created == 1
        assert Invoice.objects.count() == 4


def test_a_structure_with_nothing_compulsory_is_refused(school, setup):
    with schema_context(school.schema_name):
        empty = FeeStructure.objects.create(
            name="Optional only", session=setup["session"], term=setup["term"]
        )
        FeeItem.objects.create(
            structure=empty, name="Lunch", amount=Decimal("5000"), is_optional=True
        )
        with pytest.raises(services.NothingToBill):
            services.generate_invoices(structure=empty)


def test_payments_move_the_invoice_through_its_statuses(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        assert invoice.status == InvoiceStatus.ISSUED

        services.record_payment(
            invoice=invoice, amount=Decimal("40000.00"), method=PaymentMethod.TRANSFER
        )
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PART_PAID
        assert invoice.balance == Decimal("60000.00")

        services.record_payment(invoice=invoice, amount=Decimal("60000.00"))
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.PAID
        assert invoice.balance == Decimal("0.00")


def test_a_payment_cannot_exceed_the_balance(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        with pytest.raises(services.PaymentAmountInvalid):
            services.record_payment(invoice=invoice, amount=Decimal("100000.01"))
        assert not Payment.objects.exists()


def test_a_settled_invoice_takes_no_more_money(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        services.record_payment(invoice=invoice, amount=Decimal("100000.00"))
        invoice.refresh_from_db()
        with pytest.raises(services.InvoiceNotPayable):
            services.record_payment(invoice=invoice, amount=Decimal("1000.00"))


def test_reversing_a_payment_puts_the_balance_back(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        payment = services.record_payment(invoice=invoice, amount=Decimal("100000.00"))

        services.reverse_payment(payment, reason="Cheque bounced")
        invoice.refresh_from_db()
        assert invoice.amount_paid == Decimal("0.00")
        assert invoice.status == InvoiceStatus.ISSUED
        assert Payment.objects.get(pk=payment.pk).status == PaymentStatus.REVERSED


def test_an_invoice_with_payments_cannot_be_cancelled(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        services.record_payment(invoice=invoice, amount=Decimal("1000.00"))
        with pytest.raises(services.FinanceError):
            services.cancel_invoice(invoice, reason="Raised in error")


def test_waiving_settles_the_bill_without_money(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        invoice = services.issue_invoice(Invoice.objects.first())
        services.waive_invoice(invoice, reason="Staff child")
        invoice.refresh_from_db()
        assert invoice.status == InvoiceStatus.WAIVED
        assert invoice.is_settled


def test_the_collection_summary_adds_up(school, setup):
    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        for invoice in Invoice.objects.all():
            services.issue_invoice(invoice)
        services.record_payment(invoice=Invoice.objects.first(), amount=Decimal("50000.00"))

        summary = selectors.collection_summary(session=setup["session"], term=setup["term"])
        assert summary["billed"] == Decimal("300000.00")
        assert summary["collected"] == Decimal("50000.00")
        assert summary["outstanding"] == Decimal("250000.00")
        assert {row["category"] for row in summary["by_category"]} == {"OTHER"}


def test_the_counter_can_find_one_bill_and_the_owing_list_excludes_the_rest(school, setup):
    """The bursary's lookup, exercised through the filter backends themselves.

    Both of these are declarations — `search_fields` and a `FilterSet` method —
    and a declaration nothing reads is this codebase's recurring bug. So this
    runs the real `SearchFilter` and the real `InvoiceFilter` rather than
    asserting the attributes exist.
    """
    from django_filters.rest_framework import DjangoFilterBackend
    from rest_framework.filters import SearchFilter
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    from apps.finance.views import InvoiceViewSet

    with schema_context(school.schema_name):
        services.generate_invoices(structure=setup["structure"])
        eze = Invoice.objects.get(student__last_name="Eze")
        services.issue_invoice(eze)
        services.record_payment(invoice=eze, amount=Decimal("40000.00"))

        paid_off = Invoice.objects.exclude(pk=eze.pk).first()
        services.issue_invoice(paid_off)
        services.record_payment(invoice=paid_off, amount=Decimal("100000.00"))

        view = InvoiceViewSet()
        factory = APIRequestFactory()

        def get(params):
            # Both backends read `query_params`, which only the DRF request has.
            return Request(factory.get("/", params))

        def search(term):
            return SearchFilter().filter_queryset(
                get({"search": term}), Invoice.objects.all(), view
            )

        # By surname, by admission number, and by the number on the printed bill.
        assert [i.pk for i in search("Eze")] == [eze.pk]
        assert [i.pk for i in search(eze.student.admission_number)] == [eze.pk]
        assert [i.pk for i in search(eze.number)] == [eze.pk]

        def owing(value):
            return DjangoFilterBackend().filter_queryset(
                get({"owing": value}), Invoice.objects.all(), view
            )

        # Part-paid is still owed; settled is not; and the third invoice is a
        # DRAFT, which nobody is standing at the counter holding.
        assert [i.pk for i in owing("true")] == [eze.pk]
        assert paid_off.pk in {i.pk for i in owing("false")}
        assert eze.pk not in {i.pk for i in owing("false")}
