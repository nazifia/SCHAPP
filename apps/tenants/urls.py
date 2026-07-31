from django.urls import path

from .views import PlanListView, TenantLookupView, TenantSignupView

app_name = "tenants"

urlpatterns = [
    path("plans/", PlanListView.as_view(), name="plan-list"),
    path("tenants/", TenantSignupView.as_view(), name="tenant-signup"),
    path("tenants/lookup/", TenantLookupView.as_view(), name="tenant-lookup"),
]
