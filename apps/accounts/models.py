"""Identity. Phone number is the username.

Uniqueness is **per tenant**, not platform-wide, and that is deliberate: a
parent with children in two schools, or a teacher who also has a child
enrolled elsewhere, is completely normal here. Each schema holds its own
`accounts_user` table, so the same MSISDN can exist in two schools without
collision.

`TenantConfiguration.globally_unique_phone` used to sit next to this as a
switch for the opposite policy. It was read by nothing, and wiring it up would
have meant a platform-wide phone registry that does not exist plus breaking
the parent-at-two-schools case above, so it was removed rather than built.
"""

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.common.fields import EncryptedTextField, mask_tail
from apps.common.models import TimeStampedModel
from apps.numbering.fields import PhoneNumberField
from apps.numbering.msisdn import format_display, normalize

from .permissions_catalog import ALL, PERMISSIONS, is_known

TRIVIAL_PINS = {"000000", "111111", "123456", "654321", "121212", "112233"}


class Role(TimeStampedModel):
    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    #: Codes from apps.accounts.permissions_catalog. Validated on clean().
    permissions = models.JSONField(default=list, blank=True)
    #: System roles are seeded and may be edited but not deleted.
    is_system = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        unknown = [c for c in self.permissions if not is_known(c)]
        if unknown:
            raise ValidationError({"permissions": f"Unknown permission codes: {unknown}"})

    @property
    def grants_everything(self) -> bool:
        return ALL in self.permissions


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone: str, password: str | None = None, **extra):
        if not phone:
            raise ValueError("A phone number is required.")
        phone = normalize(phone)
        extra.setdefault("phone_display", format_display(phone))
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # OTP-only accounts are the norm
        user.save(using=self._db)
        return user

    def create_superuser(self, phone: str, password: str | None = None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("is_active", True)
        return self.create_user(phone, password, **extra)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    class Platform(models.TextChoices):
        ANDROID = "ANDROID", "Android"
        IOS = "IOS", "iOS"
        WEB = "WEB", "Web"

    phone = PhoneNumberField(unique=True, db_index=True)
    #: Local dialling form for display; never used for lookups.
    phone_display = models.CharField(max_length=24, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    email = models.EmailField(blank=True)
    first_name = models.CharField(max_length=60, blank=True)
    last_name = models.CharField(max_length=60, blank=True)
    other_name = models.CharField(max_length=60, blank=True)
    photo = models.ImageField(upload_to="avatars/", null=True, blank=True)

    #: National Identity Number. Encrypted, optional unless the tenant sets
    #: `require_nin`, and never returned by a list endpoint.
    nin = EncryptedTextField(blank=True, default="")
    nin_verified_at = models.DateTimeField(null=True, blank=True)

    #: 6-digit convenience PIN for staff, hashed with the same hasher as
    #: passwords. Never the primary factor — it is only usable after a
    #: successful OTP login on that device.
    pin_hash = models.CharField(max_length=128, blank=True)
    pin_set_at = models.DateTimeField(null=True, blank=True)

    roles = models.ManyToManyField(Role, blank=True, related_name="users")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False, help_text="Django admin access")
    last_login_ip = models.GenericIPAddressField(null=True, blank=True)

    #: NDPR consent, captured at first login rather than assumed.
    consented_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=20, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        ordering = ["last_name", "first_name"]
        indexes = [models.Index(fields=["is_active", "phone"])]

    def __str__(self) -> str:
        return self.full_name or self.phone_display or self.phone

    def save(self, *args, **kwargs):
        if self.phone and not self.phone_display:
            self.phone_display = format_display(self.phone)
        super().save(*args, **kwargs)

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.other_name, self.last_name) if p)

    def get_full_name(self) -> str:
        return self.full_name

    def get_short_name(self) -> str:
        return self.first_name or self.phone_display

    @property
    def masked_nin(self) -> str:
        return mask_tail(self.nin)

    # -- permissions ----------------------------------------------------
    @property
    def permission_codes(self) -> set[str]:
        if self.is_superuser:
            return {ALL, *PERMISSIONS}
        codes: set[str] = set()
        for role in self.roles.all():
            codes.update(role.permissions)
        return codes

    def has_perm_code(self, code: str) -> bool:
        codes = self.permission_codes
        return ALL in codes or code in codes

    # -- PIN ------------------------------------------------------------
    def set_pin(self, raw_pin: str) -> None:
        if not (raw_pin and raw_pin.isdigit() and len(raw_pin) == 6):
            raise ValidationError("Your PIN must be exactly 6 digits.")
        if raw_pin in TRIVIAL_PINS or len(set(raw_pin)) == 1:
            raise ValidationError("That PIN is too easy to guess. Choose another.")
        self.pin_hash = make_password(raw_pin)
        self.pin_set_at = timezone.now()

    def check_pin(self, raw_pin: str) -> bool:
        if not self.pin_hash:
            return False
        return check_password(raw_pin, self.pin_hash, setter=self._upgrade_pin_hash)

    def _upgrade_pin_hash(self, raw_pin: str) -> None:
        self.pin_hash = make_password(raw_pin)
        self.save(update_fields=["pin_hash", "updated_at"])


class Device(TimeStampedModel):
    """Registered devices, so a user can see and revoke their own sessions."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="devices")
    #: Client-generated, stable across app restarts.
    device_id = models.CharField(max_length=128)
    name = models.CharField(max_length=120, blank=True)
    platform = models.CharField(max_length=10, choices=User.Platform.choices, blank=True)
    app_version = models.CharField(max_length=20, blank=True)
    push_token = models.CharField(max_length=255, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "device_id"], name="unique_user_device")
        ]

    def __str__(self) -> str:
        return f"{self.name or self.platform or 'device'} ({self.device_id[:8]})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, reason: str = "") -> None:
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at", "updated_at"])
        self.token_families.filter(revoked_at__isnull=True).update(
            revoked_at=timezone.now(), revoked_reason=reason or "device revoked"
        )


class TokenFamily(TimeStampedModel):
    """One login session's chain of rotating refresh tokens.

    Rotation alone is not enough: if a refresh token is stolen and replayed
    after the legitimate client has already rotated it, both parties hold
    valid-looking chains. Seeing an already-rotated token means one of the two
    is an attacker, and we cannot tell which — so the whole family dies and
    both are forced to log in again.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="token_families")
    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="token_families"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "revoked_at"])]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def revoke(self, reason: str) -> None:
        if self.revoked_at:
            return
        self.revoked_at = timezone.now()
        self.revoked_reason = reason[:120]
        self.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])
