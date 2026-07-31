"""OTP delivery.

The code is generated *here*, in the worker, so the plaintext never passes
through the broker or the web process.
"""

import logging

from celery import shared_task
from django.utils import timezone

from apps.communication.sms import get_backend
from apps.tenants.db import schema_context
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)

MESSAGE_TEMPLATE = "{code} is your {brand} verification code. It expires in 5 minutes. Do not share it with anyone."


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def send_otp_sms(self, otp_id: str, tenant_slug: str) -> dict:
    tenant = Tenant.objects.filter(slug=tenant_slug).select_related("configuration").first()
    schema = tenant.schema_name if tenant else "public"
    config = getattr(tenant, "configuration", None)

    with schema_context(schema):
        from .models import OtpRequest
        from .services import generate_code, hash_code

        otp = OtpRequest.objects.filter(pk=otp_id).first()
        if otp is None or not otp.is_live:
            return {"result": "skipped"}

        code = generate_code()
        otp.code_hash = hash_code(str(otp.pk), code)
        otp.save(update_fields=["code_hash", "updated_at"])

        backend = get_backend(
            config.sms_backend or None if config else None,
            sender_id=(config.sms_sender_id if config else "") or "SCHAPP",
            credentials=(config.sms_credentials if config else "") or "",
        )
        brand = tenant.name if tenant else "SCHAPP"
        # OTP always goes transactional: promotional routes are blocked for
        # DND-enrolled numbers, which is most of Nigeria.
        result = backend.send(
            otp.phone, MESSAGE_TEMPLATE.format(code=code, brand=brand), route="transactional"
        )

        otp.delivered = result.ok
        otp.delivery_provider = result.provider
        otp.delivery_message_id = result.message_id
        otp.delivery_error = result.error[:200]
        otp.delivery_status = "SENT" if result.ok else "FAILED"
        otp.save(
            update_fields=[
                "delivered",
                "delivery_provider",
                "delivery_message_id",
                "delivery_error",
                "delivery_status",
                "updated_at",
            ]
        )

    if not result.ok:
        logger.warning(
            "otp delivery failed", extra={"tenant": tenant_slug, "provider": result.provider}
        )
        # ponytail: retry on the same channel only. Voice/WhatsApp fallback is
        # a Phase 8 job once a second provider is actually contracted.
        raise self.retry(exc=RuntimeError(result.error or "sms failed"))

    return {"result": "sent", "provider": result.provider}


@shared_task
def purge_expired_otps(older_than_days: int = 7) -> int:
    """NDPR data minimisation: spent codes have no reason to persist.

    OTPs live in the tenant databases, so this has to walk them: run against
    one database it would purge only that one. The platform's own is in the
    list too — it holds the challenges from platform superuser logins.
    """
    from datetime import timedelta

    from .models import OtpRequest

    cutoff = timezone.now() - timedelta(days=older_than_days)
    total = 0
    schemas = [
        None,
        *Tenant.objects.filter(provisioned_at__isnull=False).values_list("schema_name", flat=True),
    ]
    for schema in schemas:
        with schema_context(schema):
            deleted, _ = OtpRequest.objects.filter(created_at__lt=cutoff).delete()
            total += deleted
    return total
