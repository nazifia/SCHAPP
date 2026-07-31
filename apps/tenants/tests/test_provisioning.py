import pytest

from apps.tenants import db
from apps.tenants.models import Tenant, TenantStatus
from apps.tenants.services import create_tenant
from apps.tenants.tasks import provision_tenant

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


def test_signup_creates_a_pending_tenant_with_a_domain_but_no_database(settings):
    settings.BASE_DOMAIN = "myapp.ng"
    tenant = create_tenant(name="Kings College", slug="kings-college", consented=True)

    assert tenant.status == TenantStatus.PENDING
    assert tenant.schema_name == "kings_college"  # hyphens illegal in MySQL identifiers
    assert tenant.domains.get().domain == "kings-college.myapp.ng"
    assert tenant.consented_at is not None


def test_provisioning_creates_the_database_and_starts_the_trial(make_tenant):
    tenant = make_tenant("st-marys")

    assert db.database_exists(tenant.schema_name)
    assert tenant.status == TenantStatus.TRIAL
    assert tenant.provisioned_at is not None
    assert tenant.trial_ends_at > tenant.provisioned_at


def test_provisioning_twice_is_a_no_op(make_tenant):
    tenant = make_tenant("federal-poly")
    first_provisioned_at = tenant.provisioned_at

    result = provision_tenant(str(tenant.pk))

    tenant.refresh_from_db()
    assert result["result"] == "already_provisioned"
    assert tenant.provisioned_at == first_provisioned_at


def test_failed_provisioning_is_recorded_and_retryable(monkeypatch):
    from apps.tenants import services

    # Signup enqueues provisioning on commit, and tasks run eagerly here, so the
    # tenant would already be provisioned before the failure could be injected.
    monkeypatch.setattr(services, "_enqueue_provisioning", lambda tenant_id: None)
    tenant = create_tenant(name="Broken School", slug="broken-school", consented=True)
    monkeypatch.setattr(
        Tenant, "create_database", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full"))
    )

    # Called directly, Celery re-raises the original error rather than Retry.
    with pytest.raises(RuntimeError, match="disk full"):
        provision_tenant(str(tenant.pk))

    tenant.refresh_from_db()
    assert tenant.status == TenantStatus.FAILED
    assert "disk full" in tenant.provisioning_error
    assert tenant.provisioned_at is None


def test_duplicate_slug_is_rejected():
    from apps.tenants.services import SlugUnavailable

    create_tenant(name="Unity High", slug="unity-high", consented=True)
    with pytest.raises(SlugUnavailable):
        create_tenant(name="Unity High Again", slug="unity-high", consented=True)


def test_reserved_slug_is_rejected():
    from apps.tenants.services import SlugUnavailable

    with pytest.raises(SlugUnavailable):
        create_tenant(name="Sneaky", slug="public", consented=True)
