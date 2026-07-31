import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .db import schema_context
from .models import Tenant, TenantStatus
from .services import activate_trial, lapse_trial, seed_tenant, suspend

logger = logging.getLogger(__name__)

#: Statuses from which provisioning may start (or restart after a crash).
CLAIMABLE = [TenantStatus.PENDING, TenantStatus.FAILED]


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def provision_tenant(self, tenant_id: str, force: bool = False) -> dict:
    """Create the database, migrate it, seed it. Safe to run any number of times.

    Idempotency has three layers: an early return once `provisioned_at` is
    set, a compare-and-set status claim so two workers cannot both start, and
    a `CREATE DATABASE IF NOT EXISTS`/`migrate` pair plus a get_or_create-only
    seeder for anything that slips past the first two.
    """
    tenant = Tenant.objects.get(pk=tenant_id)

    if tenant.provisioned_at and not force:
        return {"tenant": tenant.slug, "result": "already_provisioned"}

    with transaction.atomic():
        claimed = (
            Tenant.objects.filter(pk=tenant_id, status__in=CLAIMABLE).update(
                status=TenantStatus.PROVISIONING
            )
            or force
        )
    if not claimed:
        # ponytail: status compare-and-set, not a lock. A worker killed mid-run
        # leaves PROVISIONING and needs `provision_tenant(..., force=True)`.
        # Swap in a MySQL GET_LOCK if that becomes routine.
        return {"tenant": tenant.slug, "result": "in_progress"}

    try:
        tenant.create_database(verbosity=0)
        with schema_context(tenant.schema_name):
            seed_tenant(tenant)
    except Exception as exc:
        Tenant.objects.filter(pk=tenant_id).update(
            status=TenantStatus.FAILED, provisioning_error=str(exc)[:2000]
        )
        logger.exception("tenant provisioning failed", extra={"tenant": tenant.slug})
        raise self.retry(exc=exc) from exc

    tenant.refresh_from_db()
    activate_trial(tenant)
    logger.info("tenant provisioned", extra={"tenant": tenant.slug})
    return {"tenant": tenant.slug, "result": "provisioned", "database": tenant.schema_name}


@shared_task(name="tenants.expire_trials")
def expire_trials() -> dict:
    """End trials that have run out, then suspend the ones nobody paid for.

    Two steps rather than one because `PAST_DUE` is servable and `SUSPENDED`
    is not: the first step is the notice, the second is the consequence, and
    `TENANT_PAST_DUE_GRACE_DAYS` is the gap between them. A school is never
    cut off on the same run that notices its trial ended.

    The grace window is measured from `trial_ends_at`, which is the only thing
    that writes `PAST_DUE` today. If a billing module ever moves a renewing
    school to `PAST_DUE`, it needs its own due date — this task will skip it
    (`trial_ends_at__isnull=True`) rather than guess one.
    """
    now = timezone.now()
    grace = timedelta(days=settings.TENANT_PAST_DUE_GRACE_DAYS)

    lapsed = []
    for tenant in Tenant.objects.filter(status=TenantStatus.TRIAL, trial_ends_at__lte=now):
        lapse_trial(tenant)
        lapsed.append(tenant.slug)

    suspended = []
    overdue = Tenant.objects.filter(
        status=TenantStatus.PAST_DUE,
        trial_ends_at__isnull=False,
        trial_ends_at__lte=now - grace,
    )
    for tenant in overdue:
        suspend(tenant, reason="Trial ended and no subscription was taken up.")
        suspended.append(tenant.slug)

    if lapsed or suspended:
        logger.info(
            "trial sweep",
            extra={"tenants_lapsed": len(lapsed), "tenants_suspended": len(suspended)},
        )
    return {"lapsed": lapsed, "suspended": suspended}
