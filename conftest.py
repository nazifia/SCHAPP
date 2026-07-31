"""Shared pytest fixtures.

Tenant fixtures create *real* databases — that is the only way an isolation
test proves anything — so they use `transaction=True` and drop the database
afterwards. Those databases are registered with Django at runtime, which is
also why pytest-django never sees them: this fixture, not the test runner,
owns their lifecycle.
"""

import contextlib

import pytest

from apps.tenants import db as tenant_db
from apps.tenants.models import Domain, Tenant, TenantConfiguration, TenantStatus


class _AliasesIncludingTenants(frozenset):
    """The aliases a test may connect to, plus any tenant registered later."""

    def __contains__(self, alias) -> bool:
        return alias.startswith("tenant_") or super().__contains__(alias)


@pytest.fixture(autouse=True, scope="session")
def _allow_tenant_databases():
    """Let tests reach the tenant databases their own fixtures just created.

    Django blocks connections to any alias a test class did not declare, and it
    reads that declaration once, when the class is built. Tenant databases are
    registered at runtime — after that point, always — so they can never appear
    in the declaration and every one of them would be refused.
    """
    from django.test import SimpleTestCase

    original = SimpleTestCase._validate_databases.__func__

    @classmethod
    def _validate_databases(cls):
        return _AliasesIncludingTenants(original(cls))

    SimpleTestCase._validate_databases = _validate_databases
    yield
    SimpleTestCase._validate_databases = classmethod(original)


@pytest.fixture
def ncc_table(monkeypatch):
    """The real NCC fixture, in memory, without a database.

    Loading the shipped JSON keeps the MSISDN tests honest about the actual
    allocation data while staying runnable anywhere.
    """
    import json
    from pathlib import Path

    from apps.numbering import selectors

    path = (
        Path(__file__).resolve().parent
        / "apps"
        / "numbering"
        / "fixtures"
        / "ncc_mobile_allocations.json"
    )
    entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    table = {
        e["ndc"]: {
            "ndc": e["ndc"],
            "operator": e.get("operator", "OTHER"),
            "nsn_length": e.get("nsn_length", 10),
            "status": e.get("status", "ASSIGNED"),
            "allows_user_accounts": e.get("allows_user_accounts", True),
        }
        for e in entries
    }
    monkeypatch.setattr(selectors, "allocation_map", lambda: table)
    return table


@pytest.fixture
def public_tenant(db):
    tenant, _ = Tenant.objects.get_or_create(
        schema_name="public",
        defaults={"name": "Platform", "slug": "public", "status": TenantStatus.ACTIVE},
    )
    Domain.objects.get_or_create(domain="testserver", tenant=tenant, defaults={"is_primary": True})
    return tenant


@pytest.fixture
def make_tenant(django_db_blocker):
    """Factory returning a fully provisioned tenant with its own database."""
    created: list[Tenant] = []

    def _make(slug: str, *, institution_type: str = "SECONDARY", status: str | None = None):
        from apps.tenants.services import create_tenant
        from apps.tenants.tasks import provision_tenant

        tenant = create_tenant(
            name=slug.replace("-", " ").title(),
            slug=slug,
            institution_type=institution_type,
            contact_email=f"{slug}@example.ng",
            consented=True,
        )
        provision_tenant(str(tenant.pk))
        tenant.refresh_from_db()
        if status:
            Tenant.objects.filter(pk=tenant.pk).update(status=status)
            tenant.refresh_from_db()
        created.append(tenant)
        return tenant

    yield _make

    with django_db_blocker.unblock():
        tenant_db.set_public()
        for tenant in created:
            tenant_db.drop_database(tenant.schema_name)
        TenantConfiguration.objects.filter(tenant__in=created).delete()
        Domain.objects.filter(tenant__in=created).delete()
        Tenant.objects.filter(pk__in=[t.pk for t in created]).delete()


@pytest.fixture(autouse=True)
def _reset_tenant():
    """No test may leak a tenant selection into the next one."""
    yield
    with contextlib.suppress(Exception):
        tenant_db.set_public()
