from django.urls import path

from .public_views import delivery_report

app_name = "communication_public"

urlpatterns = [
    path("delivery/<slug:tenant_slug>/", delivery_report, name="delivery-report"),
]
