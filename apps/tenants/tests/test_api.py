import pytest

from apps.tenants.models import Tenant, TenantStatus

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

SIGNUP = "/api/v1/public/tenants/"


def _payload(**over):
    return {
        "name": "Kings College",
        "slug": "kings-college",
        "institution_type": "SECONDARY",
        "contact_name": "Mrs Adeyemi",
        "contact_email": "head@kingscollege.ng",
        "contact_phone": "+2348031234567",
        "accept_terms": True,
        **over,
    }


def test_signup_accepts_and_queues_provisioning(client):
    response = client.post(SIGNUP, _payload(), content_type="application/json")

    assert response.status_code == 202
    body = response.json()
    assert body["slug"] == "kings-college"
    # CELERY_TASK_ALWAYS_EAGER in test settings runs provisioning inline.
    tenant = Tenant.objects.get(slug="kings-college")
    assert tenant.status == TenantStatus.TRIAL


def test_signup_without_consent_is_rejected(client):
    response = client.post(SIGNUP, _payload(accept_terms=False), content_type="application/json")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_signup_on_a_taken_slug_is_rejected(client, make_tenant):
    make_tenant("kings-college")
    response = client.post(SIGNUP, _payload(), content_type="application/json")
    assert response.status_code == 400


def test_lookup_returns_branding_and_labels_but_no_secrets(client, make_tenant):
    make_tenant("unity-poly", institution_type="TERTIARY")
    body = client.get("/api/v1/public/tenants/lookup/?slug=unity-poly").json()

    assert body["labels"]["TERM"] == "Semester"
    assert body["branding"]["primary_color"].startswith("#")
    assert "contact_email" not in body
    assert "paystack_secret_key" not in str(body)


def test_lookup_of_an_unknown_school(client):
    response = client.get("/api/v1/public/tenants/lookup/?slug=nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TENANT_NOT_FOUND"
