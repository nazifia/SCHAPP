"""Gateway webhooks.

A payment processor holds no session and cannot send `X-Tenant-Slug`, so the
school is named in the URL we registered with it:

    /api/v1/public/finance/webhook/paystack/kings-college/

That path is reachable without a tenant (it is under `PUBLIC_URL_PREFIXES`),
and the view enters the school's database itself once the signature checks
out. Three rules, all of them the hard-won kind:

* **verify before parsing.** The signature is over the raw body; anything that
  reads `request.data` first has already trusted it;
* **answer 200 for anything we cannot act on.** An unknown reference or an
  event we ignore is not the gateway's fault, and a non-2xx buys a day of
  retries for something that will never succeed;
* **never trust the amount in the redirect.** The browser comes back from
  checkout with whatever it likes; only this webhook and `verify` credit money.
"""

import json
import logging

from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.db import schema_context
from apps.tenants.models import Tenant

from . import services
from .gateways import GatewayNotConfigured, get_gateway

logger = logging.getLogger(__name__)


class PaymentWebhookView(APIView):
    """Unauthenticated by design; the signature is the credential."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(request=None, responses={200: None, 401: None, 404: None})
    def post(self, request, gateway: str, tenant_slug: str):
        tenant = Tenant.objects.filter(slug=tenant_slug).select_related("configuration").first()
        if tenant is None:
            return Response({"received": False}, status=404)

        try:
            adapter = get_gateway(gateway, tenant)
        except GatewayNotConfigured:
            return Response({"received": False}, status=404)

        if not adapter.verify_signature(request.body, request.headers):
            logger.warning(
                "rejected payment webhook", extra={"gateway": gateway, "tenant": tenant_slug}
            )
            return Response({"received": False}, status=401)

        try:
            payload = json.loads(request.body.decode() or "{}")
        except ValueError:
            return Response({"received": True, "applied": False})

        verification = adapter.parse_webhook(payload)
        if verification is None:
            return Response({"received": True, "applied": False})

        with schema_context(tenant.schema_name):
            payment = services.confirm_payment(verification, gateway_name=adapter.name)

        return Response({"received": True, "applied": payment is not None})


webhook = csrf_exempt(PaymentWebhookView.as_view())
