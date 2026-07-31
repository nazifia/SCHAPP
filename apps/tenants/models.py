"""Platform models: who the tenants are and what they may use.

Nothing here is tenant-scoped — these tables live once, in the platform
database, and are read from any tenant.
"""

from django.core.validators import RegexValidator
from django.db import models

from apps.common.fields import EncryptedTextField
from apps.common.models import TimeStampedModel

SLUG_VALIDATOR = RegexValidator(
    r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$",
    "Use 3-50 lowercase letters, digits or hyphens; must not start or end with a hyphen.",
)
SCHEMA_NAME_VALIDATOR = RegexValidator(
    r"^[a-z0-9][a-z0-9_]{0,49}$",
    "A tenant identifier is lowercase letters, digits and underscores only.",
)

# A slug becomes a hostname *and* a database name; these are reserved by one or
# the other, or by us.
RESERVED_SLUGS = {
    "public",
    "www",
    "api",
    "admin",
    "app",
    "static",
    "media",
    "mail",
    "information_schema",
    "performance_schema",
    "mysql",
    "sys",
}


class InstitutionType(models.TextChoices):
    SECONDARY = "SECONDARY", "Secondary school"
    TERTIARY = "TERTIARY", "Tertiary institution"


class TenantStatus(models.TextChoices):
    PENDING = "PENDING", "Pending provisioning"
    PROVISIONING = "PROVISIONING", "Provisioning"
    FAILED = "FAILED", "Provisioning failed"
    TRIAL = "TRIAL", "Trial"
    ACTIVE = "ACTIVE", "Active"
    #: Written by `tenants.expire_trials` when `trial_ends_at` passes. Still
    #: servable — see SERVABLE_STATUSES — for TENANT_PAST_DUE_GRACE_DAYS.
    PAST_DUE = "PAST_DUE", "Past due"
    SUSPENDED = "SUSPENDED", "Suspended"
    #: PLACEHOLDER: nothing archives a school. There is no retention policy to
    #: archive against, and a status that quietly retires a tenant's records
    #: should not be invented ahead of one.
    ARCHIVED = "ARCHIVED", "Archived"


#: Statuses whose users may still sign in and use the API.
SERVABLE_STATUSES = {TenantStatus.TRIAL, TenantStatus.ACTIVE, TenantStatus.PAST_DUE}


class Plan(TimeStampedModel):
    """Subscription tier.

    Limits are enforced at the service layer, not here — and which ones
    actually are is marked below, because a limit nobody checks is the same
    lie as a permission nobody checks. `max_students` and `max_staff` are
    enforced in `apps.people.services`; the three marked PLACEHOLDER are the
    shape of a billing module this application has not got yet.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    price_ngn = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    #: PLACEHOLDER: nothing renews a subscription, so nothing reads this.
    billing_period_days = models.PositiveIntegerField(default=365)
    trial_days = models.PositiveIntegerField(default=30)
    #: Enforced: apps.people.services.create_student / create_staff.
    max_students = models.PositiveIntegerField(null=True, blank=True, help_text="Null = unlimited")
    max_staff = models.PositiveIntegerField(null=True, blank=True)
    #: PLACEHOLDER: SMS is sent per tenant but never metered against a month.
    max_sms_per_month = models.PositiveIntegerField(null=True, blank=True)
    #: PLACEHOLDER: copied to TenantConfiguration.enabled_modules at signup and
    #: read by nothing — there is no module gate for it to drive.
    modules = models.JSONField(default=list, blank=True, help_text="Module keys this plan unlocks")
    is_public = models.BooleanField(default=True)

    class Meta:
        ordering = ["price_ngn"]

    def __str__(self) -> str:
        return self.name


class Tenant(TimeStampedModel):
    """One school. One database."""

    #: Identifier for this tenant's database: the slug with hyphens replaced,
    #: because hyphens are illegal in an unquoted MySQL identifier. Databases
    #: are created by the provisioning task, never as a save() side effect —
    #: DDL inside a request is how you get half-built tenants.
    schema_name = models.CharField(
        max_length=63, unique=True, db_index=True, validators=[SCHEMA_NAME_VALIDATOR]
    )

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=50, unique=True, validators=[SLUG_VALIDATOR])
    institution_type = models.CharField(
        max_length=20, choices=InstitutionType.choices, default=InstitutionType.SECONDARY
    )
    status = models.CharField(
        max_length=20, choices=TenantStatus.choices, default=TenantStatus.PENDING, db_index=True
    )
    plan = models.ForeignKey(Plan, null=True, blank=True, on_delete=models.PROTECT)

    contact_name = models.CharField(max_length=120, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)

    trial_ends_at = models.DateTimeField(null=True, blank=True)
    provisioned_at = models.DateTimeField(null=True, blank=True)
    provisioning_error = models.TextField(blank=True)
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True)

    # NDPR: record that someone actually agreed, and to what.
    consented_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["status", "slug"])]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    @property
    def is_servable(self) -> bool:
        return self.status in SERVABLE_STATUSES

    @property
    def is_secondary(self) -> bool:
        return self.institution_type == InstitutionType.SECONDARY

    def create_database(self, *, verbosity: int = 0) -> str:
        """Create and migrate this tenant's database. Safe to call twice."""
        from apps.tenants import db

        return db.create_database(self.schema_name, verbosity=verbosity)

    def drop_database(self) -> None:
        from apps.tenants import db

        db.drop_database(self.schema_name)


class Domain(TimeStampedModel):
    """Hostname mapped to a tenant: `<slug>.myapp.ng` or the school's own domain."""

    domain = models.CharField(max_length=253, unique=True, db_index=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="domains")
    is_primary = models.BooleanField(default=True, db_index=True)

    #: A school's own domain, rather than the `<slug>.<BASE_DOMAIN>` we issue.
    #: It only starts resolving once someone has confirmed the school actually
    #: controls it — see `is_servable` and `TenantResolutionMiddleware`.
    is_custom = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_servable(self) -> bool:
        """Whether this hostname may select its tenant.

        A custom domain is typed into the admin *before* the DNS and ownership
        checks are done — that is the ordering the two fields above exist to
        record. Serving it in the meantime means whoever currently answers for
        that hostname gets a school's session cookies scoped to it, and would
        make `verified_at` a note-to-self rather than a control. Domains we
        issue ourselves need no proof: we are the ones who issued them.
        """
        return not self.is_custom or self.verified_at is not None

    class Meta:
        ordering = ["domain"]

    def __str__(self) -> str:
        return self.domain


def default_labels() -> dict:
    return {}


class TenantConfiguration(TimeStampedModel):
    """Per-tenant settings the app needs *before* login (branding, sender ID)
    and secrets it needs at runtime (gateway keys).

    Lives in the public schema on purpose: the Flutter client fetches branding
    on the tenant-lookup screen, before any tenant context exists.
    """

    tenant = models.OneToOneField(Tenant, on_delete=models.CASCADE, related_name="configuration")

    logo = models.ImageField(upload_to="branding/", null=True, blank=True)
    primary_color = models.CharField(max_length=9, default="#0B6E4F")
    secondary_color = models.CharField(max_length=9, default="#F2A007")
    report_header = models.CharField(max_length=255, blank=True)
    motto = models.CharField(max_length=255, blank=True)
    address = models.TextField(blank=True)

    #: PLACEHOLDER: written from `Plan.modules` at signup, read by nothing.
    enabled_modules = models.JSONField(default=list, blank=True)
    # Overrides on top of the institution-type defaults, e.g. {"TERM": "Quarter"}
    label_overrides = models.JSONField(default=default_labels, blank=True)

    sms_sender_id = models.CharField(max_length=11, blank=True, help_text="Registered alphanumeric")
    sms_backend = models.CharField(max_length=100, blank=True)
    sms_credentials = EncryptedTextField(blank=True, default="")

    paystack_public_key = models.CharField(max_length=120, blank=True)
    paystack_secret_key = EncryptedTextField(blank=True, default="")
    flutterwave_public_key = models.CharField(max_length=120, blank=True)
    flutterwave_secret_key = EncryptedTextField(blank=True, default="")

    require_nin = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"Configuration for {self.tenant.slug}"
