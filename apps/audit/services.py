"""One call site for writing audit rows: `record(...)`.

Auditing must never break the request it is describing, so failures here are
logged and swallowed. An audit row that costs a user their score entry is a
worse outcome than a missing audit row.
"""

import logging
from contextlib import nullcontext
from typing import Any

from .models import AuditLog

logger = logging.getLogger(__name__)


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def actor_label(user) -> str:
    if user is None or not getattr(user, "is_authenticated", False):
        return ""
    from apps.numbering.msisdn import mask

    name = user.full_name or ""
    return f"{name} {mask(user.phone)}".strip()[:120]


def record(
    action: str,
    *,
    request=None,
    actor=None,
    obj: Any = None,
    object_type: str = "",
    object_id: str = "",
    summary: str = "",
    before: dict | None = None,
    after: dict | None = None,
    succeeded: bool = True,
    tenant_slug: str = "",
) -> AuditLog | None:
    try:
        if actor is None and request is not None:
            candidate = getattr(request, "user", None)
            actor = candidate if getattr(candidate, "is_authenticated", False) else None

        if obj is not None and not object_type:
            object_type = obj.__class__.__name__
            object_id = str(getattr(obj, "pk", ""))

        if request is not None and not tenant_slug:
            # `admin_tenant` is the school a platform superuser selected in the
            # admin; there is no `request.tenant` on the platform host.
            tenant = getattr(request, "tenant", None) or getattr(request, "admin_tenant", None)
            tenant_slug = getattr(tenant, "slug", "") or ""

        with _trail_for(actor):
            return AuditLog.objects.create(
                actor=actor,
                actor_label=actor_label(actor),
                action=action,
                object_type=object_type,
                object_id=str(object_id or ""),
                summary=summary[:255],
                before=before,
                after=after,
                ip=client_ip(request) if request is not None else None,
                user_agent=(request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""),
                device_id=(request.headers.get("X-Device-Id", "")[:128] if request else ""),
                tenant_slug=tenant_slug,
                succeeded=succeeded,
            )
    except Exception:
        logger.exception("failed to write audit entry", extra={"audit_action": action})
        return None


def _trail_for(actor):
    """Which trail the row belongs in — the school's, or the platform's.

    A platform superuser acting inside a school is acting on it from outside:
    their row is in the platform database and `actor` is a foreign key that
    cannot point across databases. So the entry goes to the platform trail,
    named by `tenant_slug` — the same shape `apps.tenants.admin_switch` already
    uses for entering and leaving a school.
    """
    from apps.accounts.platform import is_platform_user
    from apps.tenants.db import current_schema, is_public, schema_context

    if is_platform_user(actor) and not is_public(current_schema()):
        return schema_context(None)
    return nullcontext()
