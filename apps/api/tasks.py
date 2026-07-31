"""Retention for the idempotency table.

A key is only worth keeping for as long as a device might still replay it. An
outbox survives a weekend of no signal, so the window is the same seven days
the OTP purge uses — and, like that one, a table nobody purges is a table that
grows for the life of the school.
"""

import logging

from celery import shared_task
from django.utils import timezone

from apps.tenants.db import schema_context
from apps.tenants.models import Tenant

logger = logging.getLogger(__name__)


@shared_task(name="api.purge_expired_idempotency_keys")
def purge_expired_idempotency_keys(older_than_days: int = 7) -> int:
    """Delete spent keys from the platform database and every tenant's."""
    from datetime import timedelta

    from apps.api.models import IdempotencyRecord

    cutoff = timezone.now() - timedelta(days=older_than_days)
    schemas = [
        "public",
        *Tenant.objects.filter(provisioned_at__isnull=False).values_list("schema_name", flat=True),
    ]
    total = 0
    for schema in dict.fromkeys(schemas):  # the public tenant is also a row
        with schema_context(schema):
            deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
            total += deleted
    return total
