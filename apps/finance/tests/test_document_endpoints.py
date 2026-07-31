"""The invoice and receipt endpoints, over HTTP, called the way the app calls them.

`test_documents.py` renders those templates directly, which proved the layout
and nothing about the URL in front of it. Both endpoints answered 406 to
`Accept: application/pdf` — DRF negotiates content before dispatch, so with
only the JSON renderers declared the action never ran. Works in a browser,
fails from the phone, and no template test can see it.
"""

from decimal import Decimal

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Term
from apps.academics.services import enrol_student
from apps.accounts.models import Role, User
from apps.finance import services
from apps.finance.models import FeeItem, FeeStructure, Invoice
from apps.people.models import Student
from apps.tenants.db import schema_context

pytest.importorskip("xhtml2pdf", reason="PDF rendering is an optional deployment dependency")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def _headers(school, user):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def admin(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        yield user


@pytest.fixture
def settled(school):
    """One issued invoice carrying one successful payment."""
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
        student = Student.objects.create(
            admission_number="KC/25/0001",
            first_name="Ngozi",
            last_name="Ali",
            current_level=level,
            current_arm=arm,
        )
        enrol_student(student=student, session=session, level=level, class_arm=arm)

        structure = FeeStructure.objects.create(
            name="JSS1 First Term", session=session, term=term, level=level
        )
        FeeItem.objects.create(structure=structure, name="Tuition", amount=Decimal("80000.00"))
        services.generate_invoices(structure=structure)
        invoice = services.issue_invoice(Invoice.objects.get())
        payment = services.record_payment(invoice=invoice, amount=Decimal("40000.00"))
        yield {"invoice": invoice, "payment": payment}


def test_the_receipt_prints_for_a_client_asking_for_a_pdf(client, school, admin, settled):
    response = client.get(
        f"/api/v1/finance/payments/{settled['payment'].pk}/receipt/",
        HTTP_ACCEPT="application/pdf",
        **_headers(school, admin),
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_the_invoice_prints_for_a_client_asking_for_a_pdf(client, school, admin, settled):
    response = client.get(
        f"/api/v1/finance/invoices/{settled['invoice'].pk}/document/",
        HTTP_ACCEPT="application/pdf",
        **_headers(school, admin),
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_a_reversed_payment_has_no_receipt(client, school, admin, settled):
    """The error branch answers JSON, so it must survive the PDF-only Accept too."""
    with schema_context(school.schema_name):
        services.reverse_payment(settled["payment"], reason="Cheque bounced")

    response = client.get(
        f"/api/v1/finance/payments/{settled['payment'].pk}/receipt/",
        HTTP_ACCEPT="application/pdf",
        **_headers(school, admin),
    )

    assert response.status_code == 400
