"""HTML → PDF for report cards, broadsheets and transcripts.

A report card is a document a school argues about, redesigns every other
session and wants its crest on. That is a templating problem, not a drawing
problem, so the layout is a Django template and `xhtml2pdf` turns it into a
PDF — no headless browser to operate and no GTK/Pango stack to install on a
Windows box or in a slim container.

The import is deferred: everything else in this module works without the
library installed, and only asking for a PDF fails.
"""

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework.renderers import BaseRenderer, BrowsableAPIRenderer, JSONRenderer

from apps.api.exceptions import AppError


class PdfUnavailable(AppError):
    status_code = 503
    default_code = "PDF_UNAVAILABLE"
    default_detail = "PDF rendering is not available on this server."


def render_pdf(template_name: str, context: dict) -> bytes:
    """Render a template to PDF bytes."""
    try:
        from xhtml2pdf import pisa
    except ImportError as exc:  # pragma: no cover - deployment problem, not a code path
        raise PdfUnavailable() from exc

    import io

    html = render_to_string(template_name, context)
    buffer = io.BytesIO()
    result = pisa.CreatePDF(html, dest=buffer, encoding="utf-8")
    if result.err:
        raise AppError("The document could not be rendered.", code="PDF_RENDER_FAILED")
    return buffer.getvalue()


def pdf_response(template_name: str, context: dict, *, filename: str) -> HttpResponse:
    response = HttpResponse(render_pdf(template_name, context), content_type="application/pdf")
    # `inline` so the Flutter app and a browser both preview it; the client
    # decides whether to download.
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


class PdfRenderer(BaseRenderer):
    """Declares "pdf" as a format the view answers to.

    A view that reads `?format=pdf` never sees the request without this: DRF
    resolves `?format=` against the renderer list *before* dispatch and raises
    404 when nothing matches, so the branch reading it was unreachable. Those
    views return a plain `HttpResponse` from `pdf_response`, so `render` here
    is never actually called.
    """

    media_type = "application/pdf"
    format = "pdf"
    charset = None
    render_style = "binary"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return data


#: Attach to any view honouring `?format=pdf`. The first two are DRF's own
#: defaults, spelled out because this list replaces them rather than extending.
PDF_RENDERERS: list[type[BaseRenderer]] = [JSONRenderer, BrowsableAPIRenderer, PdfRenderer]


def branding(tenant) -> dict:
    """Header block for every printed document.

    Pulled from `TenantConfiguration`, which lives in the public schema, so a
    school's crest and motto reach the PDF without duplicating branding into
    every tenant schema. Labels come from the resolver, never hardcoded: the
    same template prints "Report card" for a school and "Result slip" for a
    polytechnic.
    """
    from apps.tenants.labels import labels_for

    configuration = getattr(tenant, "configuration", None)
    labels = labels_for(
        getattr(tenant, "institution_type", "SECONDARY"),
        getattr(configuration, "label_overrides", None),
    )
    return {
        "institution": getattr(tenant, "name", ""),
        "header": getattr(configuration, "report_header", "") or getattr(tenant, "name", ""),
        "motto": getattr(configuration, "motto", ""),
        "address": getattr(configuration, "address", ""),
        "logo": _logo_path(configuration),
        "primary_color": getattr(configuration, "primary_color", "#0B6E4F"),
        "labels": labels,
        "currency": settings.CURRENCY_SYMBOL,
    }


def image_path(field) -> str:
    """An absolute filesystem path — xhtml2pdf cannot fetch a URL for us.

    Returns "" for remote storage (S3 has no local path) rather than failing
    the whole document over a crest or a passport photograph.
    """
    if not field:
        return ""
    try:
        return field.path
    except (NotImplementedError, ValueError):
        return ""


def _logo_path(configuration) -> str:
    return image_path(getattr(configuration, "logo", None))
