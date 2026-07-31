"""Every action that returns a PDF must declare the PDF renderer.

Content negotiation runs before dispatch, so an action returning
`pdf_response` while its view only offers JSON answers `Accept:
application/pdf` with 406 and never runs. The endpoint then works in a browser
and fails from the phone — the only place it is used. Cheaper to assert the
renderer list than to stand up a tenant per endpoint.
"""

from apps.assessment.views import BroadsheetView, TermResultViewSet, TranscriptView
from apps.finance.views import InvoiceViewSet, PaymentViewSet
from apps.people.views import StudentViewSet

PDF_ACTIONS = [
    (InvoiceViewSet, "document"),
    (PaymentViewSet, "receipt"),
    (StudentViewSet, "id_card"),
    (StudentViewSet, "id_cards"),
    (TermResultViewSet, "report_card"),
]


def renderers_for(viewset, action_name):
    """The renderer list DRF will use for this action, action kwargs winning."""
    action = getattr(viewset, action_name)
    return getattr(action, "kwargs", {}).get("renderer_classes") or viewset.renderer_classes


def test_pdf_actions_accept_the_pdf_media_type():
    for viewset, action_name in PDF_ACTIONS:
        media_types = {renderer.media_type for renderer in renderers_for(viewset, action_name)}
        assert "application/pdf" in media_types, f"{viewset.__name__}.{action_name} would 406"


def test_pdf_only_views_accept_the_pdf_media_type():
    # ponytail: these two have no @action to hang it on — class-level list.
    for view in (BroadsheetView, TranscriptView):
        assert "application/pdf" in {r.media_type for r in view.renderer_classes}
