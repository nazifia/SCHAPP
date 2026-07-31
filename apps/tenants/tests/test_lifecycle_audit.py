"""Suspending a school is audited.

`AuditAction.TENANT_SUSPENDED` was declared from the start and written by
nothing, while `apps.audit.models` opened by saying the public copy of the
trail exists to record exactly this. Cutting a whole school off the API was
the most consequential act on the platform and the least accountable one — a
log line, which rotates and is not what `admin.view_audit` reads.
"""

import pytest

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.tenants import services
from apps.tenants.models import TenantStatus

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def test_suspension_writes_a_platform_audit_row(school):
    staff = User.objects.create_user("+2348039999999", first_name="Platform", last_name="Ops")

    services.suspend(school, reason="Non-payment since March.", actor=staff)

    entry = AuditLog.objects.filter(action=AuditAction.TENANT_SUSPENDED).get()
    assert entry.tenant_slug == school.slug
    assert entry.summary == "Non-payment since March."
    assert entry.actor_id == staff.pk
    # The transition, not just the destination: "was it already suspended?" is
    # the first question anyone asks of a row like this.
    assert entry.before == {"status": TenantStatus.TRIAL}
    assert entry.after == {"status": TenantStatus.SUSPENDED}


def test_reactivation_is_audited_too(school):
    services.suspend(school, reason="Non-payment.")
    services.reactivate(school)

    entry = AuditLog.objects.filter(action=AuditAction.TENANT_REACTIVATED).get()
    assert entry.tenant_slug == school.slug
    assert entry.before == {"status": TenantStatus.SUSPENDED}
    assert entry.after == {"status": TenantStatus.ACTIVE}


def test_the_row_lands_in_the_platform_trail_not_the_school_s(school):
    """A school's own database has no row for an act done *to* it — and a
    suspended school's admin must not be able to edit the record of why."""
    from apps.tenants.db import schema_context

    services.suspend(school, reason="Abuse report.")

    with schema_context(school.schema_name):
        assert not AuditLog.objects.filter(action=AuditAction.TENANT_SUSPENDED).exists()
    assert AuditLog.objects.filter(action=AuditAction.TENANT_SUSPENDED).exists()


def test_auditing_never_costs_the_suspension(school, monkeypatch):
    """The trail is a record of the act, not a precondition for it. A school
    that must be cut off for abuse gets cut off even if the write fails."""
    monkeypatch.setattr(
        "apps.audit.models.AuditLog.objects", property(lambda self: 1 / 0), raising=False
    )
    services.suspend(school, reason="Abuse report.")
    school.refresh_from_db()
    assert school.status == TenantStatus.SUSPENDED
