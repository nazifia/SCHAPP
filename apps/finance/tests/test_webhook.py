"""The webhook, over HTTP, with a real signature.

This is the one entry point that is unauthenticated, so it gets tested the way
Paystack will actually call it: no tenant header, no session, a signed body and
a school named only in the URL.
"""

import hashlib
import hmac
import json
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError
from django.test import Client

from apps.academics.models import AcademicSession, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.finance import services
from apps.finance.models import FeeItem, FeeStructure, Invoice, InvoiceStatus, Payment
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

SECRET = "sk_test_kings"


@pytest.fixture
def school(make_tenant, ncc_table):
    tenant = make_tenant("kings-college")
    configuration = tenant.configuration
    configuration.paystack_secret_key = SECRET
    configuration.save()
    return tenant


@pytest.fixture
def payment(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date=date(2025, 9, 1), end_date=date(2026, 7, 31)
        )
        term = Term.objects.create(
            session=session,
            index=1,
            name="First Term",
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 15),
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
        invoice = services.issue_invoice(Invoice.objects.get())
        # A checkout opened through Paystack, awaiting its webhook.
        row = Payment.objects.create(
            invoice=invoice,
            reference="RCT/26/0001",
            amount=Decimal("50000.00"),
            method="ONLINE",
            status="PENDING",
            gateway="paystack",
        )
        yield row


def _post(client, tenant_slug: str, body: dict, *, secret: str = SECRET):
    raw = json.dumps(body).encode()
    return client.post(
        f"/api/v1/public/finance/webhook/paystack/{tenant_slug}/",
        data=raw,
        content_type="application/json",
        headers={
            "x-paystack-signature": hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        },
    )


def charge(reference="RCT/26/0001", amount=50_000_00):
    return {
        "event": "charge.success",
        "data": {"reference": reference, "id": 8812, "amount": amount, "status": "success"},
    }


def test_a_signed_webhook_settles_the_invoice(school, payment):
    response = _post(Client(), school.slug, charge())
    assert response.status_code == 200
    assert response.json() == {"received": True, "applied": True}

    with schema_context(school.schema_name):
        payment.refresh_from_db()
        assert payment.status == "SUCCESS"
        assert payment.gateway_reference == "8812"
        assert Invoice.objects.get().status == InvoiceStatus.PAID


def test_an_unsigned_or_forged_webhook_changes_nothing(school, payment):
    client = Client()
    forged = _post(client, school.slug, charge(), secret="sk_test_attacker")
    assert forged.status_code == 401

    unsigned = client.post(
        f"/api/v1/public/finance/webhook/paystack/{school.slug}/",
        data=json.dumps(charge()),
        content_type="application/json",
    )
    assert unsigned.status_code == 401

    with schema_context(school.schema_name):
        payment.refresh_from_db()
        assert payment.status == "PENDING"


def test_a_replayed_webhook_credits_the_invoice_once(school, payment):
    client = Client()
    assert _post(client, school.slug, charge()).status_code == 200
    assert _post(client, school.slug, charge()).status_code == 200

    with schema_context(school.schema_name):
        invoice = Invoice.objects.get()
        assert invoice.amount_paid == Decimal("50000.00")
        assert Payment.objects.count() == 1


def test_a_webhook_for_an_unknown_school_is_a_404(school, payment):
    assert _post(Client(), "no-such-school", charge()).status_code == 404


def test_the_database_refuses_two_payments_with_one_gateway_reference(school, payment):
    """The idempotency guarantee, at the level that cannot be bypassed.

    Unconditional on purpose: MySQL silently drops a UniqueConstraint carrying
    a `condition`, so a partial index here would protect SQLite and nothing in
    production.
    """
    with schema_context(school.schema_name):
        payment.gateway_reference = "8812"
        payment.save(update_fields=["gateway_reference"])
        with pytest.raises(IntegrityError):
            Payment.objects.create(
                invoice=payment.invoice,
                reference="RCT/26/0002",
                amount=Decimal("50000.00"),
                method="ONLINE",
                gateway="paystack",
                gateway_reference="8812",
            )


def test_counter_payments_do_not_collide_on_an_empty_reference(school, payment):
    """Two cash receipts have no gateway reference and must both be allowed."""
    with schema_context(school.schema_name):
        for reference in ("RCT/26/0010", "RCT/26/0011"):
            Payment.objects.create(
                invoice=payment.invoice,
                reference=reference,
                amount=Decimal("1000.00"),
                method="CASH",
            )
        assert Payment.objects.filter(method="CASH").count() == 2


def test_a_webhook_claiming_more_than_the_checkout_does_not_credit_it(school, payment):
    """The amount ceiling `gateways/base.py` promised and nothing enforced.

    A checkout fixes its amount at `initialize`, so a gateway reporting more
    than that cannot have come from the payer. Flutterwave signs no body at all
    — `verif-hash` is a static shared secret — so an inflated payload was
    credited verbatim and settled the bill for money nobody sent.
    """
    response = _post(Client(), school.slug, charge(amount=5_000_000_00))

    assert response.status_code == 200
    assert response.json()["applied"] is True  # the row was found; it was not credited
    with schema_context(school.schema_name):
        payment.refresh_from_db()
        assert payment.status == "PENDING"
        assert payment.amount == Decimal("50000.00")
        assert "not credited" in payment.note
        assert Invoice.objects.get().status == InvoiceStatus.ISSUED


def test_a_gateway_settling_less_than_requested_still_credits_what_arrived(school, payment):
    """The other side of the same branch: under-settlement is ordinary."""
    assert _post(Client(), school.slug, charge(amount=45_000_00)).status_code == 200

    with schema_context(school.schema_name):
        payment.refresh_from_db()
        assert payment.status == "SUCCESS"
        assert payment.amount == Decimal("45000.00")
        assert Invoice.objects.get().status == InvoiceStatus.PART_PAID
