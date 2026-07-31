"""ID cards render, and each one carries its own signature.

Like the report-card tests, this feeds the template plain objects — the shape
the view builds — so a template that raises is found here rather than on the
morning the office prints four hundred cards.
"""

from types import SimpleNamespace

import pytest

from apps.assessment.pdf import branding, render_pdf
from apps.common import verification

pytest.importorskip("xhtml2pdf", reason="PDF rendering is an optional deployment dependency")

TENANT = SimpleNamespace(name="Kings College", institution_type="SECONDARY", slug="kings-college")


def card(pk="8bd1f0c2-0000-4000-8000-00000000000{}", index=1, **over):
    student_id = pk.format(index)
    return {
        "student": SimpleNamespace(full_name="Ngozi Ali", display_number="KC/25/0001"),
        "photo": "",
        "class_name": "JSS1 A",
        "programme": "",
        "session": "2025/2026",
        "valid_to": "2026-07-31",
        "verification": verification.stamp(verification.ID_CARD, student_id, tenant=TENANT),
        **over,
    }


def test_a_class_of_cards_renders_to_one_pdf():
    cards = [card(index=n) for n in range(1, 4)]
    output = render_pdf("people/id_card.html", {"cards": cards, "branding": branding(TENANT)})

    assert output.startswith(b"%PDF")


def test_each_card_is_signed_for_its_own_student():
    """A batch is not a batch of interchangeable cards: moving the photograph
    from one to another does not make its code check out."""
    first, second = card(index=1), card(index=2)
    assert first["verification"]["url"] != second["verification"]["url"]


def test_a_card_still_prints_when_the_qr_library_is_missing(monkeypatch):
    monkeypatch.setattr(verification, "qr_data_uri", lambda *a, **k: "")
    cards = [card()]
    cards[0]["verification"]["qr"] = ""

    output = render_pdf("people/id_card.html", {"cards": cards, "branding": branding(TENANT)})

    assert output.startswith(b"%PDF")
    assert cards[0]["verification"]["url"], "the URL is still printed as text"
