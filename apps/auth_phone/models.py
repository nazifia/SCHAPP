from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.common.models import TimeStampedModel
from apps.numbering.fields import PhoneNumberField


class OtpPurpose(models.TextChoices):
    LOGIN = "LOGIN", "Sign in"
    VERIFY_PHONE = "VERIFY_PHONE", "Verify phone number"
    RESET_PIN = "RESET_PIN", "Reset PIN"
    ENROL_DEVICE = "ENROL_DEVICE", "Enrol a new device"


class OtpRequest(TimeStampedModel):
    """One issued code.

    The plaintext code exists only in the SMS and in the verifying request —
    what is stored is an HMAC keyed by the server secret, so a database dump
    alone cannot be brute-forced offline despite the tiny 6-digit space.

    Rows are created even for phone numbers with no account. Skipping them
    would make response timing and behaviour differ between known and unknown
    numbers, which is exactly the enumeration oracle we are avoiding.
    """

    phone = PhoneNumberField(db_index=True)
    purpose = models.CharField(
        max_length=20, choices=OtpPurpose.choices, default=OtpPurpose.LOGIN, db_index=True
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="otp_requests",
    )
    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)

    #: False when no account matched: nothing was sent, but the request_id is
    #: still returned to the caller.
    delivered = models.BooleanField(default=False)
    delivery_provider = models.CharField(max_length=30, blank=True)
    delivery_message_id = models.CharField(max_length=120, blank=True)
    delivery_error = models.CharField(max_length=200, blank=True)
    delivery_status = models.CharField(max_length=30, blank=True)
    channel = models.CharField(max_length=20, default="sms")

    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["phone", "purpose", "-created_at"])]

    def __str__(self) -> str:
        return f"OTP {self.purpose} for {self.phone[-4:]}"

    @property
    def is_live(self) -> bool:
        return (
            self.consumed_at is None
            and self.invalidated_at is None
            and self.expires_at > timezone.now()
        )
