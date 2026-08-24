import pytest
from django.db import IntegrityError

from apps.accounts.models import User
from apps.audit.models import AuditAction, AuditLog
from apps.audit.services import record
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def test_entries_cannot_be_edited_or_deleted(school):
    with schema_context(school.schema_name):
        entry = record(AuditAction.LOGIN_OTP, summary="test")
        entry.summary = "tampered"
        with pytest.raises(IntegrityError):
            entry.save()
        with pytest.raises(IntegrityError):
            entry.delete()


def test_the_actor_phone_is_masked_not_stored_raw(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567", first_name="Amaka")
        entry = record(AuditAction.LOGIN_OTP, actor=user, summary="signed in")

        assert "+2348031234567" not in entry.actor_label
        assert entry.actor_label.endswith("4567")
        assert "Amaka" in entry.actor_label


def test_a_failure_to_audit_never_breaks_the_caller(school, monkeypatch):
    with schema_context(school.schema_name):
        monkeypatch.setattr(
            AuditLog.objects,
            "create",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("db gone")),
        )
        assert record(AuditAction.LOGIN_OTP, summary="test") is None


def test_login_writes_an_audit_row(client, school):
    with schema_context(school.schema_name):
        User.objects.create_user("+2348031234567")

    client.post(
        "/api/v1/auth/otp/request/",
        {"phone": "+2348031234567"},
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
    )

    with schema_context(school.schema_name):
        assert AuditLog.objects.filter(action=AuditAction.OTP_REQUESTED).exists()


def test_audit_rows_do_not_leak_between_schools(make_tenant, ncc_table):
    a = make_tenant("school-a")
    b = make_tenant("school-b")

    with schema_context(a.schema_name):
        record(AuditAction.LOGIN_OTP, summary="only in a")
    with schema_context(b.schema_name):
        assert not AuditLog.objects.filter(summary="only in a").exists()


def _request(xff: str | None, remote: str = "10.0.0.9"):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.META["REMOTE_ADDR"] = remote
    if xff is not None:
        request.META["HTTP_X_FORWARDED_FOR"] = xff
    return request


def test_client_ip_ignores_a_forwarded_header_nobody_terminates(settings):
    """No proxy of ours in front, so the header is the caller's to invent."""
    from apps.audit.services import client_ip

    settings.TRUSTED_PROXY_DEPTH = 0
    assert client_ip(_request("1.2.3.4")) == "10.0.0.9"
    assert client_ip(_request(None)) == "10.0.0.9"


def test_client_ip_reads_past_only_the_proxies_we_run(settings):
    """One proxy of ours: the last entry is what it observed, the rest is spoof.

    The OTP per-IP limit counts on this address, so a client prepending its own
    entries must not be able to present itself as a new caller each time.
    """
    from apps.audit.services import client_ip

    settings.TRUSTED_PROXY_DEPTH = 1
    assert client_ip(_request("1.2.3.4")) == "1.2.3.4"
    assert client_ip(_request("spoofed, spoofed-again, 1.2.3.4")) == "1.2.3.4"

    settings.TRUSTED_PROXY_DEPTH = 2
    assert client_ip(_request("spoofed, 1.2.3.4, 172.16.0.1")) == "1.2.3.4"
    # A chain shorter than the configured depth falls back to its leftmost
    # entry rather than reading off the end.
    assert client_ip(_request("1.2.3.4")) == "1.2.3.4"
