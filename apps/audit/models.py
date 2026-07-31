"""Append-only audit trail.

The table exists in every schema: the public copy records platform-level acts
(impersonation, suspension), each tenant copy records that school's own.

Append-only is enforced in Python here *and* should be enforced in Postgres in
production by revoking UPDATE/DELETE on this table from the application role —
see docs/deployment.md. Application-level enforcement stops accidents; only
the grant stops a compromised app.
"""

from django.conf import settings
from django.db import IntegrityError, models

from apps.common.models import TimeStampedModel


class AuditAction(models.TextChoices):
    LOGIN_OTP = "auth.login.otp", "Logged in with OTP"
    LOGIN_PIN = "auth.login.pin", "Logged in with PIN"
    LOGIN_FAILED = "auth.login.failed", "Failed login"
    OTP_REQUESTED = "auth.otp.requested", "OTP requested"
    OTP_LOCKED = "auth.otp.locked", "OTP locked out"
    TOKEN_REUSE = "auth.token.reuse", "Refresh token reuse detected"
    TOKEN_INVALID = "auth.token.invalid", "Refresh token rejected"
    LOGOUT = "auth.logout", "Logged out"
    PIN_SET = "auth.pin.set", "PIN set or changed"
    DEVICE_REGISTERED = "auth.device.registered", "New device registered"
    DEVICE_REVOKED = "auth.device.revoked", "Device revoked"
    ROLE_ASSIGNED = "admin.role.assigned", "Role assigned"
    STUDENTS_IMPORTED = "people.students.imported", "Students imported from a file"
    GUARDIAN_UNLINKED = "people.guardian.unlinked", "Guardian detached from a student"
    SETTINGS_CHANGED = "admin.settings.changed", "Institution settings changed"
    ARM_ASSIGNED = "academics.enrolment.arm_assigned", "Student placed in a class"
    SESSION_SET_CURRENT = "academics.session.set_current", "Current session changed"
    TERM_SET_CURRENT = "academics.term.set_current", "Current term changed"
    REGISTRATION_REJECTED = "academics.registration.rejected", "Registration refused"
    REGISTRATION_REOPENED = "academics.registration.reopened", "Refused registration reopened"
    SUBJECT_DROPPED = "academics.registration.dropped", "Course dropped"
    RESULTS_PUBLISHED = "assessment.results.published", "Results published"
    RESULTS_UNPUBLISHED = "assessment.results.unpublished", "Results withdrawn from publication"
    PROMOTION_OVERRIDDEN = "assessment.promotion.overridden", "Promotion decision overridden"
    PROMOTION_APPLIED = "assessment.promotion.applied", "Promotions applied to a new session"
    FEE_CHANGED = "finance.fee.changed", "Fee structure or fee item re-priced"
    INVOICE_ISSUED = "finance.invoice.issued", "Invoice issued"
    INVOICE_ADJUSTED = "finance.invoice.adjusted", "Invoice lines or discount adjusted"
    INVOICE_WAIVED = "finance.invoice.waived", "Invoice waived"
    INVOICE_CANCELLED = "finance.invoice.cancelled", "Invoice cancelled"
    PAYMENT_RECORDED = "finance.payment.recorded", "Payment recorded at the counter"
    PAYMENT_CONFIRMED = "finance.payment.confirmed", "Online payment confirmed"
    PAYMENT_REVERSED = "finance.payment.reversed", "Payment reversed"
    ANNOUNCEMENT_PUBLISHED = "communication.announcement.published", "Announcement published"
    SMS_SENT = "communication.sms.sent", "Bulk SMS sent"
    # PLACEHOLDER: nothing lets platform staff act *as* a named user. The two
    # actions below are the nearest thing that exists — entering a school's
    # database from the platform admin — and they are deliberately separate:
    # "I read this school's records" and "I became this parent" are different
    # claims, and collapsing them would make the trail unable to say which.
    IMPERSONATION = "platform.impersonation", "Platform staff impersonated a user"
    TENANT_ENTERED = "platform.tenant.entered", "Platform staff entered a tenant in the admin"
    TENANT_EXITED = "platform.tenant.exited", "Platform staff left a tenant in the admin"
    TENANT_TRIAL_LAPSED = "platform.tenant.trial_lapsed", "Trial ended, tenant now past due"
    TENANT_SUSPENDED = "platform.tenant.suspended", "Tenant suspended"
    TENANT_REACTIVATED = "platform.tenant.reactivated", "Tenant reactivated"


class AuditLog(TimeStampedModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_entries",
    )
    #: Denormalised so the trail stays readable after the account is deleted.
    #: Masked at write time — the raw MSISDN never lands here.
    actor_label = models.CharField(max_length=120, blank=True)

    action = models.CharField(max_length=60, db_index=True)
    object_type = models.CharField(max_length=60, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    summary = models.CharField(max_length=255, blank=True)

    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    device_id = models.CharField(max_length=128, blank=True)
    #: Set on rows in the public schema so platform actions name their target.
    tenant_slug = models.CharField(max_length=50, blank=True, db_index=True)

    succeeded = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["object_type", "object_id"]),
        ]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} by {self.actor_label or 'system'}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise IntegrityError("Audit entries are append-only and cannot be modified.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise IntegrityError("Audit entries are append-only and cannot be deleted.")
