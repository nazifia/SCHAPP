"""Document authenticity: a signed token, a verification URL, and a QR of it.

A printed transcript or an ID card leaves the building and comes back as a
photocopy. The answer is not a register of issued documents — that table grows
forever and still cannot tell you the sheet in your hand is the one it names.
It is a signature: the token carries what the document claims, signed with the
server's key, so anyone can check it, nobody can forge it, and nothing has to
be stored.

`django.core.signing` does the signing. The salt carries the tenant slug, so a
token minted by one school does not verify at another even though both run the
same code and, on a single-node deployment, the same `SECRET_KEY`.

The QR is only an encoding of the URL. `segno` is an optional dependency and
the import is deferred, exactly like `xhtml2pdf`: without it the document
still prints, with the URL in text instead of a square. A verifier who can
read is not blocked by a missing wheel.
"""

import base64
import io

from django.conf import settings
from django.core import signing
from django.utils import timezone

SALT = "schapp.document"

#: What a token may certify. Kept closed so a caller cannot invent a document
#: type that the verification endpoint has no answer for.
TRANSCRIPT = "transcript"
ID_CARD = "id_card"
KINDS = frozenset({TRANSCRIPT, ID_CARD})


class InvalidToken(Exception):
    """Tampered, truncated, or minted for another school."""


def _salt(tenant_slug: str) -> str:
    return f"{SALT}:{tenant_slug}"


def sign(kind: str, object_id, *, tenant_slug: str) -> str:
    """A URL-safe token certifying that `kind`/`object_id` was issued here."""
    if kind not in KINDS:
        raise ValueError(f"Unknown document kind: {kind}")
    return signing.dumps(
        {"k": kind, "i": str(object_id), "at": timezone.now().date().isoformat()},
        salt=_salt(tenant_slug),
        compress=True,
    )


def unsign(token: str, *, tenant_slug: str) -> dict:
    """Read a token back. Raises `InvalidToken` for anything not ours.

    No `max_age`: a transcript from 2019 is exactly the document someone needs
    verified, and the issue date travels in the payload for display.
    """
    try:
        payload = signing.loads(token, salt=_salt(tenant_slug))
    except signing.BadSignature as exc:
        raise InvalidToken(str(exc)) from exc
    if not isinstance(payload, dict) or payload.get("k") not in KINDS:
        raise InvalidToken("Unrecognised document token.")
    return {"kind": payload["k"], "id": payload["i"], "issued_on": payload.get("at", "")}


def verify_url(kind: str, object_id, *, tenant_slug: str) -> str:
    """The absolute URL a scanner opens. Public — it needs no session."""
    token = sign(kind, object_id, tenant_slug=tenant_slug)
    base = settings.DOCUMENT_VERIFY_BASE_URL.rstrip("/")
    return f"{base}/api/v1/public/verify/{tenant_slug}/{token}/"


def qr_data_uri(payload: str, *, scale: int = 3) -> str:
    """A PNG data URI of `payload`, or "" when `segno` is not installed.

    A data URI rather than a file because xhtml2pdf cannot fetch a URL for us,
    and writing the image to disk to have the renderer read it back would put
    a temporary file in the request path of every printed document.
    """
    try:
        import segno
    except ImportError:  # pragma: no cover - deployment choice, not a code path
        return ""

    buffer = io.BytesIO()
    # Error correction "m": a card that lives in a wallet gets scratched, and
    # the payload is short enough that the extra bytes cost nothing.
    segno.make(payload, error="m").save(buffer, kind="png", scale=scale, border=1)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def stamp(kind: str, object_id, *, tenant, scale: int = 3) -> dict:
    """The block a printed document needs: `{"url": ..., "qr": ...}`.

    Returns empty strings when there is no tenant — the PDF tests render the
    templates against plain objects, and a missing crest does not stop a report
    card either.
    """
    slug = getattr(tenant, "slug", "")
    if not slug:
        return {"url": "", "qr": ""}
    url = verify_url(kind, object_id, tenant_slug=slug)
    return {"url": url, "qr": qr_data_uri(url, scale=scale)}
