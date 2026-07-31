"""`Idempotency-Key` — the server half of the app's offline outbox.

The Flutter client queues writes it could not send and replays them when the
network comes back, which is at-least-once by construction: a request that
times out *after* the server committed is indistinguishable, from the device,
from one that never arrived. Replay it and the school gets a second payment
row, a second invoice, a second attendance mark.

`idempotency-key` was already listed in CORS_ALLOW_HEADERS and read by nothing
— a header the browser was allowed to send into a void. This makes it mean
something:

* first request with a key   — claim the key, run the view, store the answer;
* same key, same body        — return the stored answer, `Idempotency-Replayed`;
* same key, different body   — 422, the client has reused a key by mistake;
* same key, still running    — 409, retry in a moment;
* view answered non-2xx      — the claim is released, so a retry is a real try.

Runs innermost, after authentication and CSRF, so a rejected request never
consumes a key.
"""

import hashlib
import logging

from django.db import IntegrityError
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from apps.api.exceptions import error_payload
from apps.tenants.db import tenant_atomic

logger = logging.getLogger(__name__)

HEADER = "HTTP_IDEMPOTENCY_KEY"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_KEY_LENGTH = 190


class IdempotencyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = (request.META.get(HEADER) or "").strip()
        if not key or request.method not in UNSAFE_METHODS:
            return self.get_response(request)

        # Multipart is the CSV import and the student photo. Neither is ever
        # queued by the client (an import replayed is a second intake), and
        # reading `request.body` here would pull the whole upload into memory
        # before the view ever sees it.
        if request.content_type and request.content_type.startswith("multipart/"):
            return self.get_response(request)

        if len(key) > MAX_KEY_LENGTH:
            return _error(
                "IDEMPOTENCY_KEY_INVALID",
                f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters.",
                400,
            )

        fingerprint = _fingerprint(request)
        record, conflict = self._claim(request, key, fingerprint)
        if conflict is not None:
            return conflict

        try:
            response = self.get_response(request)
        except Exception:
            # The view blew up. Nothing was promised, so nothing is remembered.
            self._release(record)
            raise

        self._remember(record, response)
        return response

    # ------------------------------------------------------------------
    def _claim(self, request, key: str, fingerprint: str):
        """Insert the claim, or work out what the existing one means."""
        from apps.api.models import IdempotencyRecord

        try:
            # Its own transaction: an IntegrityError leaves a connection
            # unusable until the block it happened in is rolled back, and the
            # view still has to run on this connection afterwards.
            with tenant_atomic():
                record = IdempotencyRecord.objects.create(
                    key=key,
                    method=request.method,
                    path=request.path[:500],
                    request_fingerprint=fingerprint,
                )
            return record, None
        except IntegrityError:
            pass

        existing = IdempotencyRecord.objects.filter(key=key).first()
        if existing is None:
            # Lost the race and the winner has already been purged. Vanishingly
            # unlikely; treat it as in-flight rather than guessing.
            return None, _in_progress()

        if existing.request_fingerprint != fingerprint:
            return None, _error(
                "IDEMPOTENCY_KEY_REUSED",
                "This Idempotency-Key was already used for a different request.",
                422,
            )

        if existing.completed_at is None:
            return None, _in_progress()

        return None, _replay(existing)

    def _release(self, record) -> None:
        if record is None:
            return
        type(record).objects.filter(pk=record.pk).delete()

    def _remember(self, record, response) -> None:
        """Store a successful answer; release the key on anything else.

        A 4xx is deterministic — the same request will be judged the same way
        — so replaying it costs one rejected round trip and nothing else. A 5xx
        may or may not have written; releasing the key is the choice that lets
        the client try again at all.

        ponytail: no size cap on the stored body. Write endpoints answer with
        one record or a per-row bulk report; add a cap if one ever answers with
        a report-sized payload.
        """
        if record is None:
            return
        if getattr(response, "streaming", False) or not (200 <= response.status_code < 300):
            self._release(record)
            return

        record.status_code = response.status_code
        record.response_body = response.content.decode("utf-8", "replace")
        record.content_type = response.get("Content-Type", "")[:100]
        record.completed_at = timezone.now()
        # ponytail: a crash between the view's commit and this save loses the
        # record, and the replay then runs for real. Closing that needs the
        # record written inside the view's own transaction — a decorator on
        # every write endpoint, for a window measured in milliseconds.
        record.save(
            update_fields=[
                "status_code",
                "response_body",
                "content_type",
                "completed_at",
                "updated_at",
            ]
        )


def _fingerprint(request) -> str:
    """What "the same request" means: method, path *and* body.

    The body alone was the whole fingerprint, and `method`/`path` were columns
    on `IdempotencyRecord` that nothing ever compared — a declaration read by
    nobody, which is this codebase's recurring bug shape. Most write endpoints
    here are `@action`s that take no body at all (`invoices/{id}/issue/`,
    `payments/{id}/verify/`, `terms/{id}/publish/`), so one key sent to two of
    them carried byte-identical bodies, passed the reuse check, and got the
    *first* endpoint's stored response back. The second write never ran and the
    client was told it had.

    Path, not the query string: a replay is identified by what it addresses,
    and the body is where a write's payload lives.
    """
    digest = hashlib.sha256()
    digest.update(request.method.encode())
    digest.update(b"\0")
    digest.update(request.path.encode())
    digest.update(b"\0")
    digest.update(request.body)
    return digest.hexdigest()


def _replay(record) -> HttpResponse:
    response = HttpResponse(
        record.response_body,
        status=record.status_code,
        content_type=record.content_type or "application/json",
    )
    response["Idempotency-Replayed"] = "true"
    return response


def _in_progress() -> JsonResponse:
    response = _error(
        "IDEMPOTENCY_IN_PROGRESS",
        "A request with this Idempotency-Key is still being processed.",
        409,
    )
    response["Retry-After"] = "2"
    return response


def _error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse(error_payload(code, message), status=status)
