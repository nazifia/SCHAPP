"""Tenant endpoints.

Signup and lookup are the only routes in the application that answer without a
tenant context, so they are throttled hard and expose nothing beyond branding.
The settings endpoint is the opposite: it needs a resolved school and an
administrator, and it writes to the public schema from inside a tenant request
— which the router already handles, because `tenants` is a shared app.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import RequirePermission
from apps.api.exceptions import error_payload
from apps.audit.models import AuditAction
from apps.audit.services import record

from .models import Plan, TenantConfiguration
from .selectors import tenant_by_domain, tenant_by_slug
from .serializers import (
    InstitutionSettingsSerializer,
    PlanSerializer,
    TenantPublicSerializer,
    TenantSignupResultSerializer,
    TenantSignupSerializer,
)
from .services import build_domain, create_tenant


class PlanListView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(responses=PlanSerializer(many=True))
    def get(self, request):
        plans = Plan.objects.filter(is_public=True)
        return Response(PlanSerializer(plans, many=True).data)


class TenantSignupView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "tenant_signup"

    @extend_schema(
        request=TenantSignupSerializer,
        responses={202: TenantSignupResultSerializer},
        description="Register a school. Provisioning runs asynchronously; poll the lookup "
        "endpoint until status leaves PENDING/PROVISIONING.",
    )
    def post(self, request):
        serializer = TenantSignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        plan = Plan.objects.filter(code=data.get("plan_code") or "").first()
        tenant = create_tenant(
            name=data["name"],
            slug=data["slug"],
            institution_type=data["institution_type"],
            contact_name=data["contact_name"],
            contact_email=data["contact_email"],
            contact_phone=data["contact_phone"],
            plan=plan,
            consented=data["accept_terms"],
        )
        return Response(
            {
                "slug": tenant.slug,
                "name": tenant.name,
                "status": tenant.status,
                "domain": build_domain(tenant.slug),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class TenantLookupView(APIView):
    """`?slug=` or `?domain=` — powers the Flutter "find your school" screen."""

    permission_classes = [AllowAny]

    @extend_schema(responses={200: TenantPublicSerializer})
    def get(self, request):
        slug = request.query_params.get("slug")
        domain = request.query_params.get("domain")
        tenant = tenant_by_slug(slug) if slug else (tenant_by_domain(domain) if domain else None)
        if tenant is None:
            return Response(
                {
                    "error": {
                        "code": "TENANT_NOT_FOUND",
                        "message": "No institution matches that code.",
                        "details": {},
                    }
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(TenantPublicSerializer(tenant).data)


class InstitutionSettingsView(APIView):
    """`GET`/`PATCH /settings/` — the school's own branding and rules.

    `admin.manage_settings` was in the catalogue from Phase 2 with nothing
    checking it: crest, motto, colours, wording overrides and the number
    formats were all Django-admin-only, which is not where a proprietor is.

    Changes are audited. Branding is what a parent sees on a report card and
    the number format decides what every future admission number looks like —
    "who changed this" is a question that gets asked.
    """

    permission_classes = [RequirePermission("admin.manage_settings")]
    serializer_class = InstitutionSettingsSerializer

    def _configuration(self, request) -> TenantConfiguration | None:
        tenant = getattr(request, "tenant", None)
        if tenant is None:
            return None
        configuration, _ = TenantConfiguration.objects.get_or_create(tenant=tenant)
        return configuration

    @extend_schema(responses={200: InstitutionSettingsSerializer})
    def get(self, request):
        configuration = self._configuration(request)
        if configuration is None:
            return Response(error_payload("TENANT_NOT_FOUND", "No institution."), status=404)
        return Response(InstitutionSettingsSerializer(configuration).data)

    @extend_schema(
        request=InstitutionSettingsSerializer, responses={200: InstitutionSettingsSerializer}
    )
    def patch(self, request):
        configuration = self._configuration(request)
        if configuration is None:
            return Response(error_payload("TENANT_NOT_FOUND", "No institution."), status=404)

        serializer = InstitutionSettingsSerializer(configuration, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        before = InstitutionSettingsSerializer(configuration).data
        serializer.save()

        changed = {
            field: value for field, value in serializer.data.items() if before.get(field) != value
        }
        if changed:
            record(
                AuditAction.SETTINGS_CHANGED,
                request=request,
                obj=configuration,
                summary=f"Institution settings changed: {', '.join(sorted(changed))}",
                before={field: before.get(field) for field in changed},
                after=changed,
            )
        return Response(serializer.data)
