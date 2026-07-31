"""Delivery-report webhook.

Same shape as the payment webhook and for the same reason: an SMS provider
holds no session and cannot send `X-Tenant-Slug`, so the school is in the path
and the shared secret is the credential.

    /api/v1/public/communication/delivery/<tenant-slug>/

The secret is the tenant's own `sms_credentials` (the provider already knows
it), compared in constant time. A report we cannot match to a message is a
200 with `applied: false` — providers retry non-2xx, and there is nothing to
retry towards.
"""

import hmac
import logging

from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.db import schema_context
from apps.tenants.models import Tenant

from .serializers import DeliveryReportSerializer
from .services import apply_delivery_report

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Delivery-Secret"


class DeliveryReportView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(request=DeliveryReportSerializer, responses={200: None, 401: None, 404: None})
    def post(self, request, tenant_slug: str):
        tenant = Tenant.objects.filter(slug=tenant_slug).select_related("configuration").first()
        if tenant is None:
            return Response({"received": False}, status=404)

        secret = getattr(tenant.configuration, "sms_credentials", "") or ""
        sent = (request.headers.get(SECRET_HEADER) or "").strip()
        if not (secret and sent and hmac.compare_digest(secret, sent)):
            logger.warning("rejected delivery report", extra={"tenant": tenant_slug})
            return Response({"received": False}, status=401)

        serializer = DeliveryReportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with schema_context(tenant.schema_name):
            message = apply_delivery_report(
                provider_message_id=serializer.validated_data["message_id"],
                status=serializer.validated_data["status"],
            )
        return Response({"received": True, "applied": message is not None})


delivery_report = csrf_exempt(DeliveryReportView.as_view())
