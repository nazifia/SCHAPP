"""Letting a platform superuser work inside a school from the platform admin.

Django's admin is single-tenant by construction: it reads whatever database the
router hands it, and on the platform host that is always ``default``. So the
admin shows the platform's own tables — tenants, plans, the numbering plan —
and nothing of any school. `TenantOnlyAdminMixin` hides the rest rather than
letting it raise `TenantContextRequired`. That is safe and useless: support
work *is* looking at one school's data.

This adds a single control — a tenant selector in the admin header. Choosing a
school stores its slug in the session, and `AdminTenantSwitchMiddleware`
re-selects that tenant on every later ``/admin/`` request. From then on every
tenant-only ModelAdmin appears and reads and writes that school's database,
until the superuser leaves again.

Two rules make it safe to hand a superuser this much:

* **Superusers only.** Staff permissions are rows, and rows live in whichever
  database is selected — a non-superuser's permissions would silently change
  meaning at the moment of the switch. A superuser has none to change:
  ``PermissionsMixin.has_perm`` short-circuits on ``is_superuser`` before it
  reaches a database.
* **Every entry and exit is audited** in the platform trail, named by tenant.
  Reaching into a school's records is the most invasive thing this platform
  can do; it should also be the best recorded.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .db import database_exists, schema_context
from .models import Tenant

#: Where the selection is kept. The session table is in SHARED_APPS only, so
#: it always lives in the platform database — the selection therefore survives
#: the switch it causes, which is the whole reason this can work at all.
ADMIN_TENANT_SESSION_KEY = "admin_tenant_slug"

#: Must match the ``admin/`` mount in config/urls.py and the ``/admin/`` entry
#: in ``settings.PUBLIC_URL_PREFIXES``. Hardcoded rather than reversed: the
#: middleware runs on every request and the URLconf is not worth loading twice.
ADMIN_PREFIX = "/admin/"


def is_platform_superuser(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


def selectable_tenants() -> QuerySet[Tenant]:
    """Schools that can be entered: the ones with a database to enter.

    Deliberately *not* filtered by status. A suspended or past-due school is
    precisely when someone needs to look inside it.
    """
    return Tenant.objects.filter(provisioned_at__isnull=False).order_by("name")


def active_tenant(request) -> Tenant | None:
    return getattr(request, "admin_tenant", None)


@require_POST
@staff_member_required
def switch_tenant(request):
    """Enter a school, or leave it. Superusers only, POST only.

    POST ``tenant=<slug>`` to enter; POST an empty ``tenant`` to return to the
    platform. Reached from the selector in the admin header.
    """
    if not is_platform_superuser(request.user):
        raise PermissionDenied("Only a platform superuser can switch tenants.")

    slug = (request.POST.get("tenant") or "").strip().lower()
    redirect_to = _safe_next(request)

    if not slug:
        leave_tenant(request)
        return redirect(redirect_to)

    tenant = selectable_tenants().filter(slug=slug).first()
    if tenant is None:
        messages.error(request, f"No provisioned institution with the code “{slug}”.")
        return redirect(redirect_to)

    # A tenant row can outlive its database — a dropped database in
    # development, a provisioning run that half-failed. Checking once here
    # turns that into a message instead of an OperationalError on every
    # subsequent admin page.
    if not database_exists(tenant.schema_name):
        messages.error(
            request,
            f"{tenant.name} has no database yet. Re-run provisioning before entering it.",
        )
        return redirect(redirect_to)

    enter_tenant(request, tenant)
    return redirect(redirect_to)


def _safe_next(request) -> str:
    """Where to go back to — the page the selector was submitted from.

    ``next`` is a form field, so it is caller-controlled even though the form
    that normally sends it is ours: reflecting it unchecked is an open redirect
    wearing an admin session. Anything off this host falls back to the index.
    """
    candidate = request.POST.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return "admin:index"


def enter_tenant(request, tenant: Tenant) -> None:
    """Select `tenant` for this session. Also called by the Tenant changelist action."""
    previous = request.session.get(ADMIN_TENANT_SESSION_KEY) or ""
    request.session[ADMIN_TENANT_SESSION_KEY] = tenant.slug
    messages.warning(
        request,
        f"You are now working inside {tenant.name}. Everything you change here "
        "belongs to that institution.",
    )
    _audit("platform.tenant.entered", request, tenant, before=previous)


def leave_tenant(request) -> None:
    slug = request.session.pop(ADMIN_TENANT_SESSION_KEY, None)
    if not slug:
        return
    messages.info(request, "Back on the platform.")
    tenant = Tenant.objects.filter(slug=slug).first()
    if tenant is not None:
        _audit("platform.tenant.exited", request, tenant, before=slug)


def _audit(action: str, request, tenant: Tenant, *, before: str) -> None:
    """Written to the platform trail, never the school's.

    The school's own database has no row for an act done *to* it from outside,
    and `tenant_slug` on a public row is exactly the field that names the
    target — the same shape `apps.tenants.services` uses for suspension.
    """
    from apps.audit.services import record

    with schema_context(None):
        record(
            action,
            request=request,
            obj=tenant,
            summary=f"{request.user} in the platform admin",
            before={"admin_tenant": before},
            after={"admin_tenant": tenant.slug if action.endswith("entered") else ""},
            tenant_slug=tenant.slug,
        )


def admin_tenant(request):
    """Template context for the header selector.

    Registered as a context processor, so it runs for every template render —
    including the API's rare HTML responses. It answers with an empty dict for
    anyone who is not a platform superuser, which is also every anonymous hit
    on the login page.
    """
    user = getattr(request, "user", None)
    if not is_platform_superuser(user):
        return {}
    return {
        "admin_tenant": active_tenant(request),
        "admin_tenant_choices": selectable_tenants(),
    }
