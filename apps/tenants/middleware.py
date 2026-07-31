"""Tenant resolution — runs before authentication, fails closed.

Three ways in, in priority order:

1. ``X-Tenant-Slug`` header      — the Flutter app's path.
2. Hostname                      — subdomain or a school's custom domain (web).
3. ``tenant_slug`` JWT claim     — never *selects* a tenant, only cross-checks
   the one already resolved, so a stolen token cannot be replayed against
   another school.

Anything that resolves to nothing and is not an explicitly public path gets
``TENANT_NOT_FOUND``. There is deliberately no "default to public" fallback:
that failure mode leaks the platform database to unknown hosts.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from apps.api.exceptions import error_payload
from apps.tenants import db
from apps.tenants.models import Domain, Tenant

logger = logging.getLogger(__name__)

CODE_NOT_FOUND = "TENANT_NOT_FOUND"
CODE_SUSPENDED = "TENANT_SUSPENDED"
CODE_MISMATCH = "TENANT_MISMATCH"


class TenantResolutionError(Exception):
    def __init__(self, code: str, message: str, status: int, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status
        self.details = details or {}


class TenantResolutionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.header_key = "HTTP_" + settings.TENANT_HEADER.upper().replace("-", "_")

    def __call__(self, request):
        try:
            tenant = self._resolve(request)
        except TenantResolutionError as exc:
            db.set_public()
            return JsonResponse(
                error_payload(exc.code, exc.message, exc.details), status=exc.status
            )

        request.tenant = tenant
        db.set_tenant(tenant)

        try:
            return self.get_response(request)
        finally:
            # The selection is thread-local and worker threads are reused: a
            # tenant left set would bleed into the next request on this thread.
            db.set_public()

    # ------------------------------------------------------------------
    def _resolve(self, request):
        public_schema = settings.PUBLIC_SCHEMA_NAME
        from_header = self._from_header(request)
        from_host = self._from_host(request)

        if from_header and from_host and from_header.pk != from_host.pk:
            raise TenantResolutionError(
                CODE_MISMATCH,
                "The tenant header does not match this address.",
                403,
                {"header": from_header.slug, "host": from_host.slug},
            )

        tenant = from_header or from_host

        if tenant is None or tenant.schema_name == public_schema:
            if tenant is None and not self._is_public_path(request.path):
                raise TenantResolutionError(
                    CODE_NOT_FOUND, "No institution matches this address.", 404
                )
            return None

        if not tenant.is_servable:
            raise TenantResolutionError(
                CODE_SUSPENDED,
                "This institution's account is not active. Contact your administrator.",
                403,
                {"status": tenant.status},
            )

        self._assert_token_matches(request, tenant)
        return tenant

    def _from_header(self, request):
        slug = (request.META.get(self.header_key) or "").strip().lower()
        if not slug:
            return None
        tenant = Tenant.objects.filter(slug=slug).first()
        if tenant is None:
            raise TenantResolutionError(
                CODE_NOT_FOUND, "No institution matches this code.", 404, {"slug": slug}
            )
        return tenant

    def _from_host(self, request):
        hostname = request.get_host().split(":")[0].lower()
        domain = Domain.objects.select_related("tenant").filter(domain=hostname).first()
        # An unverified custom domain is treated as no domain at all: it is a
        # hostname a school *claims*, and until that claim is checked, serving
        # it would hand the school's cookies to whoever answers for it today.
        if domain is not None and not domain.is_servable:
            domain = None
        if domain is None or domain.tenant.schema_name == settings.PUBLIC_SCHEMA_NAME:
            # The platform host (localhost, the marketing domain) selects no
            # school, so a header on it is a choice, not a mismatch.
            return None
        return domain.tenant

    def _assert_token_matches(self, request, tenant) -> None:
        """A token minted for school A must never be usable against school B.

        Signature verification still happens later in DRF; this is the early,
        cheap cross-check so the wrong schema is never even selected.
        """
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("bearer "):
            return
        try:
            from rest_framework_simplejwt.tokens import UntypedToken

            claims = UntypedToken(header.split(" ", 1)[1].strip())
        except Exception:
            return  # invalid/expired: let DRF produce the 401
        claim = claims.get("tenant_slug")
        if claim and claim != tenant.slug:
            raise TenantResolutionError(
                CODE_MISMATCH, "This session belongs to a different institution.", 403
            )

    @staticmethod
    def _is_public_path(path: str) -> bool:
        return any(path.startswith(p) for p in settings.PUBLIC_URL_PREFIXES)


class AdminTenantSwitchMiddleware:
    """Applies the tenant a platform superuser picked in the admin header.

    Sits *after* `django.contrib.auth`'s middleware, and that ordering is the
    whole design. `apps.accounts` is listed in both halves of the app split, so
    the user table exists in the platform database and in every school's — and
    once a tenant is selected, the session's user id resolves against *that
    school's* table, where the platform superuser has no row. The superuser
    would appear logged out the instant they switched.

    So the user is read first, from `default`, and the tenant selected only
    afterwards. `request.user` is lazy, so reading an attribute off it here is
    not a formality: it is what forces the lookup to happen on the right
    database while the right database is still selected. Every later access
    returns the same cached object.

    See `apps.tenants.admin_switch` for the selector itself.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant = self._selected(request)
        if tenant is None:
            return self._no_tenant_selected(request) or self.get_response(request)

        request.admin_tenant = tenant
        db.set_tenant(tenant)
        try:
            return self.get_response(request)
        finally:
            # `TenantResolutionMiddleware` clears the selection on its own way
            # out too, but it is outside this one and a response rendered
            # lazily must not find a tenant still selected.
            db.set_public()

    @staticmethod
    def _no_tenant_selected(request):
        """Send a superuser to the selector instead of a bare 403.

        A tenant-only ModelAdmin with no tenant selected has no database to
        read, so `TenantOnlyAdminMixin` answers False to every permission
        question and the admin raises `PermissionDenied`. That is right, and it
        is unreadable: a superuser who follows a bookmark into
        `/admin/academics/classarm/` gets "403 Forbidden" for a page they are
        in fact entitled to — they simply have not said *which school*.

        Only superusers are redirected. For anyone else the 403 is the honest
        answer, selection or no selection.
        """
        from .admin_switch import ADMIN_PREFIX, is_platform_superuser

        if not request.path.startswith(ADMIN_PREFIX):
            return None
        if getattr(request, "tenant", None) is not None:
            return None
        # `/admin/<app_label>/…` — the same tenant-only set `gate_tenant_only_admins`
        # wraps. Apps in both halves keep their platform copy and need no tenant.
        label = request.path[len(ADMIN_PREFIX) :].split("/")[0]
        if label not in db.tenant_labels() - db.shared_labels():
            return None
        if not is_platform_superuser(getattr(request, "user", None)):
            return None

        messages.warning(
            request,
            "Choose an institution before opening its records — "
            f"“{label}” belongs to a school, not to the platform.",
        )
        return redirect("admin:index")

    @staticmethod
    def _selected(request):
        from .admin_switch import ADMIN_PREFIX, ADMIN_TENANT_SESSION_KEY, is_platform_superuser

        if not request.path.startswith(ADMIN_PREFIX):
            return None

        # A school's own host (`<slug>.<BASE_DOMAIN>/admin/`) already resolved
        # a tenant upstream. The selector exists only on the platform host,
        # where none was — overriding a host-resolved tenant would let the
        # header and the address disagree, which the resolver treats as an
        # attack everywhere else.
        if getattr(request, "tenant", None) is not None:
            return None

        slug = request.session.get(ADMIN_TENANT_SESSION_KEY)
        if not slug:
            return None

        if not is_platform_superuser(getattr(request, "user", None)):
            # Demoted, deactivated, or never entitled: drop the selection
            # rather than carry it into the next request.
            request.session.pop(ADMIN_TENANT_SESSION_KEY, None)
            return None

        tenant = Tenant.objects.filter(slug=slug).first()
        if tenant is None:
            request.session.pop(ADMIN_TENANT_SESSION_KEY, None)
        return tenant
