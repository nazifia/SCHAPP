from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.api.health import healthz, readyz
from apps.tenants.admin_switch import switch_tenant

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    # Before the admin mount, so `admin.site.urls`' catch-all never claims it.
    path("admin/switch-tenant/", switch_tenant, name="admin-switch-tenant"),
    path("admin/", admin.site.urls),
    path("api/v1/", include(("apps.api.urls", "api"), namespace="v1")),
]

if getattr(settings, "SERVE_API_DOCS", settings.DEBUG):
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/docs/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]

if settings.DEBUG:
    urlpatterns += [path("__debug__/", include("debug_toolbar.urls"))]
