from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.tenants.views import InstitutionSettingsView

from .reporting import AnalyticsView, ExportView
from .verify import verify_document

reports = DefaultRouter()
reports.register("overview", AnalyticsView, basename="overview")
reports.register("export", ExportView, basename="export")

urlpatterns = [
    # Public schema: reachable without a tenant (signup, tenant lookup, and the
    # provider webhooks, which name their school in the path).
    path("public/", include("apps.tenants.urls")),
    path("public/finance/", include("apps.finance.public_urls")),
    path("public/communication/", include("apps.communication.public_urls")),
    # Printed-document verification: the school is in the path because the
    # person scanning the QR has no app, no account and no tenant header.
    path("public/verify/<slug:tenant_slug>/<str:token>/", verify_document, name="verify-document"),
    # Tenant-scoped: the middleware must already have resolved a school.
    path("auth/", include("apps.auth_phone.urls")),
    path("admin/", include("apps.accounts.urls")),
    path("admin/audit/", include("apps.audit.urls")),
    path("settings/", InstitutionSettingsView.as_view(), name="institution-settings"),
    path("people/", include("apps.people.urls")),
    path("academics/", include("apps.academics.urls")),
    path("attendance/", include("apps.attendance.urls")),
    path("assessment/", include("apps.assessment.urls")),
    path("finance/", include("apps.finance.urls")),
    path("communication/", include("apps.communication.urls")),
    path("reports/", include(reports.urls)),
]
