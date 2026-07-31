"""Single error envelope for the whole API.

    {"error": {"code": "...", "message": "...", "details": {...}}}

The Flutter client branches on `code` only; `message` is for humans and is
free to change or be localised.
"""

from functools import lru_cache
from typing import Any

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class AppError(APIException):
    """Base for errors that carry a stable machine code."""

    #: Annotated as `int`, not left to be inferred as the literal 400, so a
    #: subclass declaring 403 is an override rather than a type error.
    status_code: int = status.HTTP_400_BAD_REQUEST
    default_code = "BAD_REQUEST"
    default_detail = "Request could not be processed."

    def __init__(self, message: str | None = None, details: Any = None, code: str | None = None):
        super().__init__(message or self.default_detail)
        self.code = code or self.default_code
        self.details = details or {}


class TenantNotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "TENANT_NOT_FOUND"
    default_detail = "No institution matches this address."


class TenantSuspended(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "TENANT_SUSPENDED"
    default_detail = "This institution's account is not active."


class TenantMismatch(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "TENANT_MISMATCH"
    default_detail = "Credentials do not belong to this institution."


_DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
}


def error_payload(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def envelope_exception_handler(exc, context) -> Response | None:
    response = drf_exception_handler(exc, context)
    if response is None:
        return None  # unhandled -> Django's 500 path, so Sentry still sees it

    if isinstance(exc, AppError):
        code, details, message = exc.code, exc.details, str(exc.detail)
    else:
        code = getattr(exc, "default_code", "").upper() or _DEFAULT_CODES.get(
            response.status_code, "ERROR"
        )
        if isinstance(response.data, dict) and "detail" in response.data:
            message, details = str(response.data["detail"]), {}
        else:
            # Serializer validation: keep the field map in details.
            message, details = "Validation failed.", response.data
            code = "VALIDATION_ERROR"

    response.data = error_payload(code, message, details)
    if response.status_code == 401:
        _audit_rejected_refresh(context, code, message)
    if response.status_code == 429:
        wait = getattr(exc, "wait", None)
        if wait:
            response["Retry-After"] = int(wait)
    return response


@lru_cache(maxsize=1)
def _refresh_path() -> str:
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse("v1:auth_phone:token-refresh")
    except NoReverseMatch:  # pragma: no cover - only if the route is renamed
        return ""


def _audit_rejected_refresh(context, code: str, message: str) -> None:
    """Audit a 401 that DRF raised *before* the refresh view could run.

    Everywhere else a 401 is routine — it is how a client discovers its access
    token has aged out, and recording that would write a row per user per
    fifteen minutes. On the refresh endpoint it is not routine: a client has no
    reason to send a bearer token there at all, so this is either a bug in the
    client or someone probing.

    ``TokenRefreshView`` audits the failures it can see. Authentication runs
    ahead of permissions, so an unusable bearer token on this ``AllowAny`` route
    never reaches the view, and this is the only place left that can say so.
    """
    path = getattr(context.get("request") if context else None, "path", "")
    if not path or path != _refresh_path():
        return

    from apps.audit.models import AuditAction
    from apps.audit.services import record

    # The underlying Django request, not DRF's: reading `.user` off the DRF one
    # re-runs the authentication that just failed and raises again, and
    # `record` would swallow that into no audit row at all.
    request = context["request"]
    record(
        AuditAction.TOKEN_INVALID,
        request=getattr(request, "_request", request),
        summary=f"Refresh rejected before the view ran: {code} — {message}",
        succeeded=False,
    )
