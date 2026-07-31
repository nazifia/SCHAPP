import pytest

from apps.tenants.models import TenantStatus

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


def test_health_endpoint_needs_no_tenant(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_host_on_a_tenant_path_fails_closed(client):
    response = client.get("/api/v1/me/", HTTP_HOST="nowhere.example.com")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"


def test_unknown_slug_header_is_rejected(client):
    response = client.get("/api/v1/public/plans/", HTTP_X_TENANT_SLUG="does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"


def test_header_resolves_the_tenant(client, make_tenant):
    tenant = make_tenant("kings-college")
    response = client.get("/healthz", HTTP_X_TENANT_SLUG=tenant.slug)
    assert response.status_code == 200


def test_header_and_host_pointing_at_different_tenants_is_a_mismatch(client, make_tenant):
    a = make_tenant("school-a")
    make_tenant("school-b")

    response = client.get(
        "/healthz",
        HTTP_X_TENANT_SLUG="school-b",
        HTTP_HOST=a.domains.get().domain,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_MISMATCH"


@pytest.mark.parametrize("status", [TenantStatus.SUSPENDED, TenantStatus.ARCHIVED])
def test_suspended_tenants_are_locked_out(client, make_tenant, status):
    tenant = make_tenant("late-payer", status=status)
    response = client.get("/healthz", HTTP_X_TENANT_SLUG=tenant.slug)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TENANT_SUSPENDED"


def test_past_due_tenants_are_still_served(client, make_tenant):
    # Nigerian schools pay in bursts; a late invoice must not lock out a term's
    # results on the day they are due.
    tenant = make_tenant("grace-period", status=TenantStatus.PAST_DUE)
    assert client.get("/healthz", HTTP_X_TENANT_SLUG=tenant.slug).status_code == 200


def test_the_tenant_is_reset_after_the_response(client, make_tenant):
    from apps.tenants import db

    tenant = make_tenant("reset-check")
    client.get("/healthz", HTTP_X_TENANT_SLUG=tenant.slug)
    assert db.current_schema() == "public"


def test_an_unverified_custom_domain_does_not_resolve(client, make_tenant):
    """`Domain.is_custom` and `verified_at` were recorded and never checked.

    A custom domain is typed into the admin before anyone confirms the school
    controls it. Serving it in that window gives whoever answers for that
    hostname today a session scoped to the school — and made `verified_at` a
    note-to-self rather than a control.
    """
    tenant = make_tenant("kings-college")
    tenant.domains.create(domain="portal.kingscollege.ng", is_custom=True, is_primary=False)

    response = client.get("/api/v1/me/", HTTP_HOST="portal.kingscollege.ng")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"


def test_a_verified_custom_domain_resolves(client, make_tenant):
    from django.utils import timezone

    tenant = make_tenant("kings-college")
    tenant.domains.create(
        domain="portal.kingscollege.ng",
        is_custom=True,
        is_primary=False,
        verified_at=timezone.now(),
    )
    assert client.get("/healthz", HTTP_HOST="portal.kingscollege.ng").status_code == 200


def test_the_domain_we_issued_needs_no_verification(client, make_tenant):
    """`<slug>.<BASE_DOMAIN>` is created unverified by `create_tenant` — we
    issued it, so requiring proof would lock every school out of its own
    address on day one."""
    tenant = make_tenant("kings-college")
    domain = tenant.domains.get()
    assert domain.verified_at is None and not domain.is_custom
    assert client.get("/healthz", HTTP_HOST=domain.domain).status_code == 200


def test_a_header_on_the_platform_host_is_not_a_mismatch(client, make_tenant):
    # The Flutter dev client talks to localhost, which is the public tenant's
    # domain; the slug header has to still pick the school.
    tenant = make_tenant("kings-college")
    response = client.get("/healthz", HTTP_X_TENANT_SLUG=tenant.slug, HTTP_HOST="localhost")
    assert response.status_code == 200
