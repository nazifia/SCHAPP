"""A trial that never ends is not a trial.

`activate_trial` has stamped `trial_ends_at` on every school since Phase 1 and
nothing ever compared it to the clock: `Plan.trial_days` was computed, stored,
shown on the plan list, and enforced by nothing. `TenantStatus.PAST_DUE` and
`ARCHIVED` were declared and written by nothing, which is the same lie one
level up.
"""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditAction, AuditLog
from apps.tenants.models import TenantStatus
from apps.tenants.tasks import expire_trials

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def _age_trial(tenant, days_ago: int):
    tenant.trial_ends_at = timezone.now() - timedelta(days=days_ago)
    tenant.save(update_fields=["trial_ends_at", "updated_at"])


def test_a_running_trial_is_left_alone(school):
    assert school.status == TenantStatus.TRIAL
    assert school.trial_ends_at > timezone.now()

    assert expire_trials() == {"lapsed": [], "suspended": []}

    school.refresh_from_db()
    assert school.status == TenantStatus.TRIAL


def test_an_expired_trial_goes_past_due_and_still_serves(school):
    _age_trial(school, 1)

    assert expire_trials()["lapsed"] == [school.slug]

    school.refresh_from_db()
    assert school.status == TenantStatus.PAST_DUE
    # The point of the middle state: the school keeps working while it pays.
    assert school.is_servable
    assert AuditLog.objects.filter(action=AuditAction.TENANT_TRIAL_LAPSED).count() == 1


@override_settings(TENANT_PAST_DUE_GRACE_DAYS=14)
def test_the_grace_window_is_respected_then_enforced(school):
    _age_trial(school, 1)
    expire_trials()

    # Day 13 of a 14-day grace: still past due, still servable.
    _age_trial(school, 13)
    assert expire_trials()["suspended"] == []
    school.refresh_from_db()
    assert school.status == TenantStatus.PAST_DUE

    _age_trial(school, 15)
    assert expire_trials()["suspended"] == [school.slug]
    school.refresh_from_db()
    assert school.status == TenantStatus.SUSPENDED
    assert not school.is_servable
    assert AuditLog.objects.filter(action=AuditAction.TENANT_SUSPENDED).count() == 1


def test_the_sweep_is_idempotent(school):
    _age_trial(school, 60)
    expire_trials()
    expire_trials()
    expire_trials()

    school.refresh_from_db()
    assert school.status == TenantStatus.SUSPENDED
    assert AuditLog.objects.filter(action=AuditAction.TENANT_TRIAL_LAPSED).count() == 1
    assert AuditLog.objects.filter(action=AuditAction.TENANT_SUSPENDED).count() == 1


def test_a_past_due_tenant_without_a_trial_date_is_never_guessed_at(school):
    """The grace window is measured from `trial_ends_at`. A future billing
    module that writes PAST_DUE for a renewal must supply its own due date
    rather than inherit a suspension from this task."""
    school.status = TenantStatus.PAST_DUE
    school.trial_ends_at = None
    school.save(update_fields=["status", "trial_ends_at", "updated_at"])

    assert expire_trials()["suspended"] == []
    school.refresh_from_db()
    assert school.status == TenantStatus.PAST_DUE


def test_a_reactivated_school_is_not_lapsed_again(school):
    from apps.tenants import services

    _age_trial(school, 60)
    expire_trials()
    services.reactivate(school)

    expire_trials()

    school.refresh_from_db()
    assert school.status == TenantStatus.ACTIVE
