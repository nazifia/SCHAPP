"""Tenant lifecycle. All writes to Tenant/Domain go through here."""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.api.exceptions import AppError

from .models import (
    RESERVED_SLUGS,
    Domain,
    InstitutionType,
    Plan,
    Tenant,
    TenantConfiguration,
    TenantStatus,
)

logger = logging.getLogger(__name__)

CONSENT_VERSION = "2026-01"


class SlugUnavailable(AppError):
    default_code = "SLUG_UNAVAILABLE"
    default_detail = "That school code is already taken."


def build_domain(slug: str) -> str:
    return f"{slug}.{settings.BASE_DOMAIN}"


def assert_slug_available(slug: str) -> None:
    if slug in RESERVED_SLUGS:
        raise SlugUnavailable("That school code is reserved.")
    if Tenant.objects.filter(slug=slug).exists():
        raise SlugUnavailable()
    if Domain.objects.filter(domain=build_domain(slug)).exists():
        raise SlugUnavailable()


@transaction.atomic
def create_tenant(
    *,
    name: str,
    slug: str,
    institution_type: str = InstitutionType.SECONDARY,
    contact_name: str = "",
    contact_email: str = "",
    contact_phone: str = "",
    plan: Plan | None = None,
    consented: bool = False,
) -> Tenant:
    """Register a school. Does *not* create the database — that is the task's job.

    Kept transactional and DDL-free so a signup request stays fast and a
    failure leaves no half-built database behind.
    """
    slug = slug.strip().lower()
    assert_slug_available(slug)

    tenant = Tenant.objects.create(
        schema_name=slug.replace("-", "_"),  # hyphens are illegal in MySQL identifiers
        name=name.strip(),
        slug=slug,
        institution_type=institution_type,
        status=TenantStatus.PENDING,
        contact_name=contact_name,
        contact_email=contact_email,
        contact_phone=contact_phone,
        plan=plan,
        consented_at=timezone.now() if consented else None,
        consent_version=CONSENT_VERSION if consented else "",
    )
    Domain.objects.create(domain=build_domain(slug), tenant=tenant, is_primary=True)
    TenantConfiguration.objects.create(
        tenant=tenant,
        enabled_modules=list(plan.modules) if plan else [],
    )

    transaction.on_commit(lambda: _enqueue_provisioning(tenant.pk))
    return tenant


def _enqueue_provisioning(tenant_id) -> None:
    from .tasks import provision_tenant

    provision_tenant.delay(str(tenant_id))


def activate_trial(tenant: Tenant) -> Tenant:
    days = tenant.plan.trial_days if tenant.plan else 30
    tenant.status = TenantStatus.TRIAL
    tenant.trial_ends_at = timezone.now() + timedelta(days=days)
    tenant.provisioned_at = tenant.provisioned_at or timezone.now()
    tenant.provisioning_error = ""
    tenant.save(
        update_fields=[
            "status",
            "trial_ends_at",
            "provisioned_at",
            "provisioning_error",
            "updated_at",
        ]
    )
    return tenant


def lapse_trial(tenant: Tenant, *, actor=None) -> Tenant:
    """Trial is over: PAST_DUE, which is still servable.

    `activate_trial` has dated the end of every trial since Phase 1 and
    nothing ever compared that date to the clock, so `Plan.trial_days` was a
    number the platform computed, stored, displayed and never enforced —
    every school that ever signed up had an unlimited free account. The two
    statuses that end a trial, `PAST_DUE` and `ARCHIVED`, were likewise
    written by nothing.

    The step is deliberately not `suspend`: `PAST_DUE` is in
    `SERVABLE_STATUSES`, so a school whose trial ran out on a Friday keeps
    working while somebody sorts out payment. `expire_trials` is what
    suspends it if nobody does.
    """
    before = tenant.status
    tenant.status = TenantStatus.PAST_DUE
    tenant.save(update_fields=["status", "updated_at"])
    logger.info("tenant trial lapsed", extra={"tenant": tenant.slug})
    _audit_lifecycle(
        "platform.tenant.trial_lapsed",
        tenant,
        actor,
        before,
        summary=f"Trial ended {timezone.localtime(tenant.trial_ends_at):%Y-%m-%d}."
        if tenant.trial_ends_at
        else "Trial ended.",
    )
    return tenant


def suspend(tenant: Tenant, reason: str, *, actor=None) -> Tenant:
    """Lock a school out. Every user of it stops working, so this is audited.

    `AuditAction.TENANT_SUSPENDED` existed from the start and nothing ever
    wrote it — `apps.audit.models` opens by saying the public copy of the
    trail is there to record suspension, and the only record was a log line.
    Logs rotate, are not queryable, and are not what `admin.view_audit`
    exposes. Cutting a school off is the single most consequential act on the
    platform; it should not be the least accountable.
    """
    before = tenant.status
    tenant.status = TenantStatus.SUSPENDED
    tenant.suspended_at = timezone.now()
    tenant.suspension_reason = reason
    tenant.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])
    logger.warning("tenant suspended", extra={"tenant": tenant.slug})
    _audit_lifecycle("platform.tenant.suspended", tenant, actor, before, summary=reason)
    return tenant


def reactivate(tenant: Tenant, *, actor=None) -> Tenant:
    before = tenant.status
    tenant.status = TenantStatus.ACTIVE
    tenant.suspended_at = None
    tenant.suspension_reason = ""
    tenant.save(update_fields=["status", "suspended_at", "suspension_reason", "updated_at"])
    _audit_lifecycle("platform.tenant.reactivated", tenant, actor, before)
    return tenant


def _audit_lifecycle(action: str, tenant: Tenant, actor, before: str, summary: str = "") -> None:
    """Written to the *platform* trail, which is where `tenant_slug` names the
    school — the school's own database has no row for an act done to it."""
    from apps.audit.services import record
    from apps.tenants.db import schema_context

    with schema_context(None):
        record(
            action,
            actor=actor,
            obj=tenant,
            summary=summary or str(tenant),
            before={"status": before},
            after={"status": tenant.status},
            tenant_slug=tenant.slug,
        )


def seed_tenant(tenant: Tenant) -> None:
    """Populate a freshly migrated tenant database with its starting data.

    Runs inside the tenant database and must be safe to run twice: every seeder
    here is get_or_create-only, so re-provisioning after a crash never
    duplicates a level or overwrites a grading scale the school has edited.
    """
    from apps.academics.seeds import seed_structure
    from apps.accounts.services import seed_roles
    from apps.assessment.seeds import seed_grading

    roles = seed_roles()
    levels = seed_structure(tenant.institution_type)
    components = seed_grading(tenant.institution_type)
    logger.info(
        "seeded tenant",
        extra={
            "tenant": tenant.slug,
            "roles_created": roles,
            "levels": levels,
            "components": components,
        },
    )
