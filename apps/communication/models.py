"""Announcements and the log of everything the school sent out.

Two tables and a template, because the hard part of school messaging is not
sending — it is *who*, and afterwards *did it arrive*.

* **who**: an announcement names an audience (roles, and optionally a class or
  a level), and `selectors.recipients_for` turns that into people at publish
  time. The list is resolved once and stored as `Message` rows, so a pupil who
  transfers next week does not retroactively change who was told;
* **did it arrive**: every send is a `Message` row with the provider's id on
  it, which is what the delivery-report webhook updates. "We sent 412 SMS,
  9 failed" is a query, not a guess.

SMS costs real money per unit in Nigeria and a school will query the bill, so
nothing here fans out without leaving a row behind.
"""

from django.conf import settings
from django.db import models

from apps.common.models import TimeStampedModel


class Channel(models.TextChoices):
    IN_APP = "IN_APP", "In-app"
    SMS = "SMS", "SMS"
    EMAIL = "EMAIL", "Email"
    PUSH = "PUSH", "Push notification"


class MessageStatus(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    SENT = "SENT", "Sent to provider"
    DELIVERED = "DELIVERED", "Delivered"
    FAILED = "FAILED", "Failed"


class MessageTemplate(TimeStampedModel):
    """Reusable body with `{placeholders}`.

    Schools send the same six messages all year — fees due, result published,
    absent today. A template keeps the wording in one place and out of code, so
    changing "Dear Parent" to "Dear Sir/Ma" is not a deploy.

    PLACEHOLDER: templates are stored, listed and edited, and no send path
    reads one — `services.render` exists and nothing calls it with a template.
    Whatever gives them a sender must filter on `is_active` at that point, or
    the switch below is the next declaration nobody checks.
    """

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    channel = models.CharField(max_length=10, choices=Channel.choices, default=Channel.SMS)
    subject = models.CharField(max_length=160, blank=True, help_text="Email only")
    body = models.TextField(help_text="Placeholders: {name} {student} {amount} {term} {school}")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.channel})"


class Announcement(TimeStampedModel):
    """A notice, and optionally a broadcast.

    In-app is always on: an announcement is readable in the app whether or not
    the school also paid to text it. The other channels are opt-in per notice,
    because SMS to nine hundred parents is a real invoice.
    """

    title = models.CharField(max_length=160)
    body = models.TextField()
    #: Role codes that should see it. Empty means everyone in the school.
    audience_roles = models.JSONField(default=list, blank=True)
    level = models.ForeignKey(
        "academics.ClassLevel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    class_arm = models.ForeignKey(
        "academics.ClassArm",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="announcements",
    )
    #: Channels to broadcast on beyond in-app, e.g. ["SMS", "PUSH"].
    channels = models.JSONField(default=list, blank=True)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="announcements",
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_pinned = models.BooleanField(default=False)
    #: Filled in by `publish`: how many people the audience resolved to.
    recipients_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-is_pinned", "-published_at", "-created_at"]
        indexes = [models.Index(fields=["published_at", "expires_at"])]

    def __str__(self) -> str:
        return self.title

    @property
    def is_published(self) -> bool:
        return self.published_at is not None


class Message(TimeStampedModel):
    """One outbound message to one person on one channel.

    Kept even when it fails — a parent who says "I was never told" is answered
    from this table, and a provider's invoice is reconciled against it.
    """

    channel = models.CharField(max_length=10, choices=Channel.choices, db_index=True)
    #: Phone number, email address or push token, depending on the channel.
    destination = models.CharField(max_length=255)
    subject = models.CharField(max_length=160, blank=True)
    body = models.TextField()

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    announcement = models.ForeignKey(
        Announcement, null=True, blank=True, on_delete=models.CASCADE, related_name="messages"
    )
    template = models.ForeignKey(
        MessageTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="messages"
    )

    status = models.CharField(
        max_length=10, choices=MessageStatus.choices, default=MessageStatus.QUEUED, db_index=True
    )
    provider = models.CharField(max_length=30, blank=True)
    #: The provider's id, which is what a delivery report arrives keyed by.
    provider_message_id = models.CharField(max_length=120, blank=True, db_index=True)
    delivery_status = models.CharField(max_length=30, blank=True)
    error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    #: In-app messages are read in the app; the rest never set this.
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "channel", "read_at"]),
            models.Index(fields=["status", "-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.channel} → {self.destination} ({self.status})"
