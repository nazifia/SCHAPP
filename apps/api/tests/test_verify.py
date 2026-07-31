"""The public document-verification endpoint.

Tested over HTTP rather than as a service, because being reachable without a
session, without the tenant header and without CSRF *is* the feature: the
person scanning the QR is an employer with a phone camera.
"""

import pytest

from apps.common import verification
from apps.people.models import Student
from apps.tenants.db import schema_context
from apps.tenants.models import Tenant, TenantStatus

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def student(school):
    with schema_context(school.schema_name):
        yield Student.objects.create(
            admission_number="KC/25/0001", first_name="Ngozi", last_name="Ali"
        )


def _url(slug, token):
    return f"/api/v1/public/verify/{slug}/{token}/"


def test_a_card_verifies_without_any_credential(client, school, student):
    token = verification.sign(verification.ID_CARD, student.pk, tenant_slug=school.slug)

    body = client.get(_url(school.slug, token)).json()

    assert body["verified"] is True
    assert body["institution"] == school.name
    assert body["name"] == "Ngozi Ali"
    assert body["number"] == "KC/25/0001"
    # Confirming the sheet, not answering a lookup: nothing here that the
    # document in the holder's hand does not already print.
    assert "date_of_birth" not in body
    assert "phone" not in body


def test_a_forged_token_is_answered_not_crashed(client, school, student):
    token = verification.sign(verification.ID_CARD, student.pk, tenant_slug=school.slug)
    forged = token[:-1] + ("A" if token[-1] != "A" else "B")

    response = client.get(_url(school.slug, forged))

    # 200 with verified:false — a scanner that shows a browser error page has
    # told the holder nothing.
    assert response.status_code == 200
    assert response.json() == {"verified": False, "reason": "INVALID_SIGNATURE"}


def test_another_school_s_token_does_not_verify_here(client, school, student, make_tenant):
    other = make_tenant("unity-poly")
    token = verification.sign(verification.ID_CARD, student.pk, tenant_slug=school.slug)

    body = client.get(_url(other.slug, token)).json()

    assert body == {"verified": False, "reason": "INVALID_SIGNATURE"}


def test_a_signed_token_for_a_record_that_is_gone_says_so(client, school):
    token = verification.sign(
        verification.TRANSCRIPT, "8bd1f0c2-0000-4000-8000-000000000000", tenant_slug=school.slug
    )

    body = client.get(_url(school.slug, token)).json()

    assert body == {"verified": False, "reason": "NOT_ON_RECORD"}


def test_a_suspended_school_verifies_nothing(client, school, student):
    token = verification.sign(verification.ID_CARD, student.pk, tenant_slug=school.slug)
    Tenant.objects.filter(pk=school.pk).update(status=TenantStatus.SUSPENDED)

    body = client.get(_url(school.slug, token)).json()

    assert body == {"verified": False, "reason": "UNKNOWN_INSTITUTION"}


def test_an_unknown_school_leaks_nothing(client):
    body = client.get(_url("no-such-school", "whatever")).json()
    assert body == {"verified": False, "reason": "UNKNOWN_INSTITUTION"}
