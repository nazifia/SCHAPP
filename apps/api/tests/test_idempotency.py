"""A replayed write is not a second write.

The outbox on the device is at-least-once by construction: a request that
times out after the server committed cannot be told apart, from the phone,
from one that never arrived. `Idempotency-Key` is what stops the replay from
producing a second row, and until this middleware existed the header was
listed in CORS_ALLOW_HEADERS and read by nothing.
"""

import pytest

from apps.academics.models import AcademicSession
from apps.accounts.models import Role, User
from apps.api.models import IdempotencyRecord
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

BODY = {"name": "2025/2026", "start_date": "2025-09-01", "end_date": "2026-07-31"}
URL = "/api/v1/academics/sessions/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("unity-high")


@pytest.fixture
def headers(school):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348032222222", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


def _post(client, headers, body=BODY, key="k-outbox-0001"):
    extra = dict(headers)
    if key is not None:
        extra["HTTP_IDEMPOTENCY_KEY"] = key
    return client.post(URL, body, content_type="application/json", **extra)


def test_the_same_key_twice_writes_once(client, headers, school):
    first = _post(client, headers)
    assert first.status_code == 201, first.content

    second = _post(client, headers)
    assert second.status_code == 201
    assert second.headers["Idempotency-Replayed"] == "true"
    # The replay is the *first* answer, not a fresh one.
    assert second.json()["id"] == first.json()["id"]

    with schema_context(school.schema_name):
        assert AcademicSession.objects.filter(name="2025/2026").count() == 1


def test_no_key_means_no_record(client, headers, school):
    """The header is opt-in: a caller that omits it is untouched, and nothing
    is written to the table on its behalf."""
    assert _post(client, headers, key=None).status_code == 201
    with schema_context(school.schema_name):
        assert not IdempotencyRecord.objects.exists()


def test_a_key_reused_for_a_different_body_is_rejected(client, headers):
    assert _post(client, headers).status_code == 201
    response = _post(client, headers, body={**BODY, "name": "2026/2027"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_a_key_reused_on_a_different_endpoint_is_rejected(client, headers, school):
    """The body alone was the fingerprint, and most write endpoints here are
    `@action`s that take no body at all — so one key sent to two of them passed
    the reuse check and got the *first* endpoint's answer back. The second write
    never ran and the client was told it had.
    """
    assert _post(client, headers).status_code == 201

    # Byte-identical body, same key, a different path. The middleware runs
    # before the view, so what that endpoint would have made of the payload
    # never comes into it: this is refused as a reuse, not replayed.
    other = client.post(
        "/api/v1/academics/terms/",
        BODY,
        content_type="application/json",
        **{**headers, "HTTP_IDEMPOTENCY_KEY": "k-outbox-0001"},
    )
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert "Idempotency-Replayed" not in other.headers


def test_the_same_key_and_body_on_the_same_path_still_replays(client, headers, school):
    """Folding method and path in must not break the case the header exists for."""
    first = _post(client, headers, key="k-outbox-0003")
    second = _post(client, headers, key="k-outbox-0003")
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json()["id"] == first.json()["id"]


def test_a_failed_write_releases_the_key(client, headers, school):
    """A rejected request must not burn the key it was sent with."""
    bad = _post(client, headers, body={**BODY, "start_date": "not-a-date"})
    assert bad.status_code == 400

    with schema_context(school.schema_name):
        assert not IdempotencyRecord.objects.filter(key="k-outbox-0001").exists()

    # The client fixes the payload and retries under the same key.
    assert _post(client, headers).status_code == 201


def test_an_unfinished_claim_answers_409(client, headers, school):
    """The in-flight case, forced: a claim exists with no response stored.

    This is what a concurrent duplicate hits — the first request is still
    running, so there is nothing to replay and nothing safe to run. Reached by
    running a real request and then reopening its record, which keeps the
    fingerprint honest: a hand-written one would trip the reuse check first.
    """
    assert _post(client, headers).status_code == 201
    with schema_context(school.schema_name):
        IdempotencyRecord.objects.filter(key="k-outbox-0001").update(completed_at=None)

    response = _post(client, headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert response.headers["Retry-After"] == "2"

    # Still one session: the 409 refused, it did not run the write again.
    with schema_context(school.schema_name):
        assert AcademicSession.objects.filter(name="2025/2026").count() == 1


def test_keys_are_scoped_to_one_school(client, headers, make_tenant, school):
    """Two schools using the same key must not read each other's answers.

    Isolation comes from the record living in the tenant's own database, which
    is the whole reason `apps.api` is listed in TENANT_APPS.
    """
    from apps.auth_phone import tokens

    other = make_tenant("demo-college")
    with schema_context(other.schema_name):
        user = User.objects.create_user("+2348033333333", first_name="Head")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=other)

    assert _post(client, headers).status_code == 201
    second = _post(
        client,
        {
            "HTTP_X_TENANT_SLUG": other.slug,
            "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
        },
    )
    assert second.status_code == 201
    assert "Idempotency-Replayed" not in second.headers

    for tenant in (school, other):
        with schema_context(tenant.schema_name):
            assert AcademicSession.objects.filter(name="2025/2026").count() == 1


def test_a_get_ignores_the_header(client, headers):
    """Safe methods are already idempotent; claiming a key for them would only
    make a second page load answer 409."""
    response = client.get(URL, HTTP_IDEMPOTENCY_KEY="k-outbox-0001", **headers)
    assert response.status_code == 200
