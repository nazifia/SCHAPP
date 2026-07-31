"""The NCC Mobile Number Allocation Table, as data.

Lives in the public schema: the numbering plan is national, not per-school.

Nothing in this codebase may hardcode a Nigerian prefix. When the NCC assigns
a new block, the fix is `manage.py sync_ncc_allocations` with an updated
fixture — a data change, not a deploy.
"""

from django.db import models

from apps.common.models import TimeStampedModel


class Operator(models.TextChoices):
    MTN = "MTN", "MTN"
    GLO = "GLO", "Globacom"
    AIRTEL = "AIRTEL", "Airtel"
    EMTS = "EMTS", "9mobile (EMTS)"
    MAFAB = "MAFAB", "Mafab (Mcom)"
    MTEL = "MTEL", "M-Tel"
    SMILE = "SMILE", "Smile"
    #: 0700 and 0800 are shared blocks held by value-added-service licensees,
    #: not by a network. Never a personal line.
    VAS_SNS = "VAS_SNS", "VAS / SNS licensees (shared)"
    OTHER = "OTHER", "Other / historic"


class AllocationStatus(models.TextChoices):
    ASSIGNED = "ASSIGNED", "Assigned"
    WITHDRAWN = "WITHDRAWN", "Withdrawn"
    RESERVED = "RESERVED", "Reserved"
    AVAILABLE = "AVAILABLE", "Available"


class MobileNumberAllocation(TimeStampedModel):
    #: National Destination Code as the NCC publishes it, with the trunk 0:
    #: "0803", "0913", and occasionally five digits such as "07025".
    ndc = models.CharField(max_length=6, unique=True, db_index=True)
    operator = models.CharField(max_length=10, choices=Operator.choices, default=Operator.OTHER)
    #: Digits after the country code, trunk 0 excluded. 10 for Nigerian mobile.
    nsn_length = models.PositiveSmallIntegerField(default=10)
    status = models.CharField(
        max_length=12, choices=AllocationStatus.choices, default=AllocationStatus.ASSIGNED
    )
    #: 0700 is VAS / shared-cost. Valid numbers, but never a person's line, so
    #: they must not become login identities.
    allows_user_accounts = models.BooleanField(default=True)
    source_url = models.URLField(blank=True)
    last_verified_at = models.DateField(null=True, blank=True)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["ndc"]
        verbose_name = "mobile number allocation"

    def __str__(self) -> str:
        return f"{self.ndc} — {self.get_operator_display()} ({self.status})"

    @property
    def is_usable_for_login(self) -> bool:
        return self.status == AllocationStatus.ASSIGNED and self.allows_user_accounts
