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
