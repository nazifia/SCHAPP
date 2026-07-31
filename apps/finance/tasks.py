"""Scheduled finance work.

One task: re-verify online payments whose webhook never landed. It runs per
tenant because the money and the gateway keys are both per tenant.
"""

import logging

from celery import shared_task

from apps.tenants.db import schema_context
from apps.tenants.models import SERVABLE_STATUSES, Tenant

from .services import sweep_pending

logger = logging.getLogger(__name__)


@shared_task(name="finance.sweep_pending_payments")
def sweep_pending_payments(tenant_slug: str = "") -> dict:
    """Sweep one school, or every servable one when given no slug."""
    tenants = Tenant.objects.filter(status__in=SERVABLE_STATUSES)
    if tenant_slug:
        tenants = tenants.filter(slug=tenant_slug)

    totals = {"checked": 0, "settled": 0, "abandoned": 0}
    for tenant in tenants.select_related("configuration"):
        try:
            with schema_context(tenant.schema_name):
                result = sweep_pending(tenant=tenant)
        except Exception:
            # One school's gateway being down must not stop the others.
            logger.exception("payment sweep failed", extra={"tenant": tenant.slug})
            continue
        for key in totals:
            totals[key] += result[key]
    return totals
