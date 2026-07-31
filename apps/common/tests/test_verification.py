"""Document signatures. No database: this is arithmetic over a secret key.

The three things that must hold for a printed transcript to mean anything: a
token we minted verifies, an edited one does not, and one school's token is
not another school's.
"""

import pytest
from django.test import override_settings

from apps.common import verification


def test_a_token_round_trips_with_what_it_certifies():
    token = verification.sign(verification.TRANSCRIPT, "abc-123", tenant_slug="kings-college")
    claim = verification.unsign(token, tenant_slug="kings-college")

    assert claim["kind"] == verification.TRANSCRIPT
    assert claim["id"] == "abc-123"
    assert claim["issued_on"], "the issue date travels in the payload, not just the signature"


def test_an_edited_token_is_refused():
    token = verification.sign(verification.ID_CARD, "abc-123", tenant_slug="kings-college")
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(verification.InvalidToken):
        verification.unsign(forged, tenant_slug="kings-college")


def test_a_token_does_not_verify_at_another_school():
    """The salt carries the slug, so a shared SECRET_KEY is not a shared trust."""
    token = verification.sign(verification.TRANSCRIPT, "abc-123", tenant_slug="kings-college")

    with pytest.raises(verification.InvalidToken):
        verification.unsign(token, tenant_slug="unity-poly")


def test_an_unknown_document_kind_is_a_programming_error():
    with pytest.raises(ValueError):
        verification.sign("payslip", "abc-123", tenant_slug="kings-college")


@override_settings(DOCUMENT_VERIFY_BASE_URL="https://schapp.ng")
def test_the_printed_url_is_absolute_and_public():
    url = verification.verify_url(verification.ID_CARD, "abc-123", tenant_slug="kings-college")
    assert url.startswith("https://schapp.ng/api/v1/public/verify/kings-college/")


def test_the_qr_is_an_inline_png_a_pdf_renderer_can_embed():
    pytest.importorskip("segno", reason="QR rendering is an optional dependency")
    assert verification.qr_data_uri("https://example.ng/x").startswith("data:image/png;base64,")


def test_a_document_without_a_tenant_still_renders():
    """The PDF tests feed the templates plain objects; a missing crest does not
    stop a report card and a missing QR must not stop a transcript."""
    assert verification.stamp(verification.TRANSCRIPT, "abc", tenant=None) == {"url": "", "qr": ""}
