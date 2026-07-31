"""End-to-end phone login against a real tenant schema."""

import re
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import User
from apps.auth_phone import ratelimit
from apps.auth_phone.models import OtpRequest
from apps.communication.sms.console import LocMemBackend
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

PHONE = "+2348031234567"
REQUEST_URL = "/api/v1/auth/otp/request/"
VERIFY_URL = "/api/v1/auth/otp/verify/"


@pytest.fixture(autouse=True)
def _clean_state():
    cache.clear()
    LocMemBackend.outbox.clear()
    yield
    cache.clear()
    LocMemBackend.outbox.clear()


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def teacher(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user(PHONE, first_name="Amaka", last_name="Obi")
        from apps.accounts.services import assign_role

        assign_role(user, "teacher")
    return user


def _headers(school):
    return {"HTTP_X_TENANT_SLUG": school.slug}


def _last_code() -> str:
    match = re.search(r"\b(\d{6})\b", LocMemBackend.outbox[-1]["message"])
    assert match, "no code in the outbound SMS"
    return match.group(1)


def _login(client, school, phone: str = PHONE):
    response = client.post(
        REQUEST_URL, {"phone": phone}, content_type="application/json", **_headers(school)
    )
    assert response.status_code == 200
    request_id = response.json()["request_id"]
    return client.post(
        VERIFY_URL,
        {"request_id": request_id, "code": _last_code(), "device": {"device_id": "dev-1"}},
        content_type="application/json",
        **_headers(school),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_otp_login_issues_tokens_and_returns_the_user(client, school, teacher):
    response = _login(client, school)

    assert response.status_code == 200
    body = response.json()
    assert body["access"] and body["refresh"]
    assert body["user"]["full_name"] == "Amaka Obi"
    assert body["user"]["roles"] == ["teacher"]
    assert "assessment.enter_score" in body["user"]["permissions"]


def test_the_sms_goes_out_on_the_transactional_route(client, school, teacher):
    client.post(REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school))
    sent = LocMemBackend.outbox[-1]
    # DND-enrolled numbers only receive transactional traffic.
    assert sent["route"] == "transactional"
    assert sent["to"] == PHONE


def test_any_dialling_variant_reaches_the_same_account(client, school, teacher):
    for variant in ["08031234567", "8031234567", "+234 803 123 4567", "234 0803 123 4567"]:
        cache.clear()
        response = _login(client, school, phone=variant)
        assert response.status_code == 200, variant


def test_plaintext_code_is_never_stored(client, school, teacher):
    client.post(REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school))
    code = _last_code()
    with schema_context(school.schema_name):
        otp = OtpRequest.objects.get()
    assert otp.code_hash and code not in otp.code_hash


def test_verifying_marks_the_phone_verified(client, school, teacher):
    _login(client, school)
    with schema_context(school.schema_name):
        teacher.refresh_from_db()
    assert teacher.phone_verified_at is not None


# ---------------------------------------------------------------------------
# No enumeration
# ---------------------------------------------------------------------------
def test_unknown_number_gets_the_same_response_and_no_sms(client, school):
    known = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    )
    LocMemBackend.outbox.clear()
    cache.clear()
    unknown = client.post(
        REQUEST_URL,
        {"phone": "+2348039999999"},
        content_type="application/json",
        **_headers(school),
    )

    assert unknown.status_code == known.status_code == 200
    assert unknown.json().keys() == known.json().keys()
    assert LocMemBackend.outbox == []  # nothing sent, nothing leaked


def test_correct_looking_code_for_an_unknown_number_still_fails(client, school):
    response = client.post(
        REQUEST_URL,
        {"phone": "+2348039999999"},
        content_type="application/json",
        **_headers(school),
    )
    request_id = response.json()["request_id"]
    verify = client.post(
        VERIFY_URL,
        {"request_id": request_id, "code": "123456"},
        content_type="application/json",
        **_headers(school),
    )
    assert verify.status_code == 401
    assert verify.json()["error"]["code"] == "OTP_INVALID"


# ---------------------------------------------------------------------------
# Expiry, reuse, wrong codes
# ---------------------------------------------------------------------------
def test_expired_code_is_rejected(client, school, teacher):
    response = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    )
    request_id = response.json()["request_id"]
    code = _last_code()
    with schema_context(school.schema_name):
        OtpRequest.objects.filter(pk=request_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    verify = client.post(
        VERIFY_URL,
        {"request_id": request_id, "code": code},
        content_type="application/json",
        **_headers(school),
    )
    assert verify.status_code == 401
    assert verify.json()["error"]["code"] == "OTP_EXPIRED"


def test_a_code_cannot_be_used_twice(client, school, teacher):
    response = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    )
    request_id = response.json()["request_id"]
    code = _last_code()
    payload = {"request_id": request_id, "code": code}

    first = client.post(VERIFY_URL, payload, content_type="application/json", **_headers(school))
    second = client.post(VERIFY_URL, payload, content_type="application/json", **_headers(school))

    assert first.status_code == 200
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "OTP_ALREADY_USED"


def test_requesting_a_new_code_invalidates_the_previous_one(client, school, teacher):
    first = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    ).json()["request_id"]
    first_code = _last_code()

    ratelimit.reset("otp_phone_burst", PHONE)
    client.post(REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school))

    verify = client.post(
        VERIFY_URL,
        {"request_id": first, "code": first_code},
        content_type="application/json",
        **_headers(school),
    )
    assert verify.status_code == 401
    assert verify.json()["error"]["code"] == "OTP_INVALID"


def test_five_wrong_codes_trigger_a_thirty_minute_lockout(client, school, teacher):
    request_id = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    ).json()["request_id"]

    for _ in range(5):
        response = client.post(
            VERIFY_URL,
            {"request_id": request_id, "code": "000000"},
            content_type="application/json",
            **_headers(school),
        )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "OTP_LOCKED_OUT"
    assert int(response["Retry-After"]) > 1500
    assert ratelimit.is_locked_out(PHONE) > 0


# ---------------------------------------------------------------------------
# Rate limits over HTTP
# ---------------------------------------------------------------------------
def test_second_request_within_a_minute_is_throttled(client, school, teacher):
    client.post(REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school))
    second = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    )

    assert second.status_code == 429
    assert second.json()["error"]["code"] == "OTP_RATE_LIMITED"
    assert "Retry-After" in second


def _verify(client, school, request_id: str, code: str):
    return client.post(
        VERIFY_URL,
        {"request_id": request_id, "code": code, "device": {"device_id": "dev-1"}},
        content_type="application/json",
        **_headers(school),
    )


def _request_id(client, school) -> str:
    response = client.post(
        REQUEST_URL, {"phone": PHONE}, content_type="application/json", **_headers(school)
    )
    assert response.status_code == 200
    return response.json()["request_id"]


def test_dev_code_works_only_while_debug_is_on(client, school, teacher, settings):
    settings.OTP_DEV_CODE = "000000"

    settings.DEBUG = False
    assert _verify(client, school, _request_id(client, school), "000000").status_code == 401

    ratelimit.clear_lockout(PHONE)
    ratelimit.reset("otp_phone_burst", PHONE)
    settings.DEBUG = True
    assert _verify(client, school, _request_id(client, school), "000000").status_code == 200


def test_invalid_phone_is_rejected_before_anything_is_issued(client, school):
    response = client.post(
        REQUEST_URL, {"phone": "0803123"}, content_type="application/json", **_headers(school)
    )
    assert response.status_code == 400
    with schema_context(school.schema_name):
        assert OtpRequest.objects.count() == 0
    assert LocMemBackend.outbox == []
