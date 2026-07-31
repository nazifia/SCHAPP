"""The invoice and the receipt render, without a database.

A parent is holding the receipt; it cannot be the thing that raises on the
Friday everyone pays. Same approach as the assessment documents: feed the
templates plain objects of the shape the views build, and check real PDF bytes
come out — which also catches a template that fails to compile at all.
"""

from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.assessment.pdf import branding, render_pdf

pytest.importorskip("xhtml2pdf", reason="PDF rendering is an optional deployment dependency")

TENANT = SimpleNamespace(name="Kings College", institution_type="SECONDARY", slug="kings-college")


def invoice(**overrides):
    defaults = {
        "number": "INV/26/0001",
        "student": SimpleNamespace(full_name="Ngozi Ali", display_number="KC/25/0001"),
        "term": SimpleNamespace(name="First Term"),
        "session": SimpleNamespace(name="2025/2026"),
        "status": "PART_PAID",
        "subtotal": Decimal("105000.00"),
        "discount": Decimal("0.00"),
        "total": Decimal("105000.00"),
        "amount_paid": Decimal("40000.00"),
        "balance": Decimal("65000.00"),
        "issued_at": datetime(2025, 9, 12, 9, 0),
        "due_date": date(2025, 10, 3),
        "note": "",
    }
    return SimpleNamespace(**{**defaults, **overrides})


LINES = [
    SimpleNamespace(
        description="Tuition",
        amount=Decimal("85000.00"),
        get_category_display=lambda: "Tuition",
    ),
    SimpleNamespace(
        description="Development levy",
        amount=Decimal("20000.00"),
        get_category_display=lambda: "Development levy",
    ),
]


def payment(**overrides):
    defaults = {
        "reference": "RCT/26/0007",
        "amount": Decimal("40000.00"),
        "paid_at": datetime(2025, 9, 20, 11, 30),
        "gateway": "paystack",
        "note": "",
        "payer_name": "Mrs Ali",
        "get_method_display": lambda: "Online (card / transfer / USSD)",
        "get_status_display": lambda: "Successful",
    }
    return SimpleNamespace(**{**defaults, **overrides})


def test_an_invoice_renders_to_pdf():
    pdf = render_pdf(
        "finance/invoice.html",
        {
            "invoice": invoice(),
            "lines": LINES,
            "payments": [payment()],
            "branding": branding(TENANT),
        },
    )
    assert pdf.startswith(b"%PDF")


def test_an_invoice_with_no_payments_yet_still_renders():
    pdf = render_pdf(
        "finance/invoice.html",
        {
            "invoice": invoice(status="ISSUED", amount_paid=Decimal("0.00")),
            "lines": LINES,
            "payments": [],
            "branding": branding(TENANT),
        },
    )
    assert pdf.startswith(b"%PDF")


def test_a_receipt_renders_to_pdf():
    pdf = render_pdf(
        "finance/receipt.html",
        {"payment": payment(), "invoice": invoice(), "branding": branding(TENANT)},
    )
    assert pdf.startswith(b"%PDF")
