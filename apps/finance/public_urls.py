from django.urls import path

from .public_views import webhook

app_name = "finance_public"

urlpatterns = [
    # The school is in the path because a gateway cannot send our tenant header.
    path("webhook/<str:gateway>/<slug:tenant_slug>/", webhook, name="payment-webhook"),
]
