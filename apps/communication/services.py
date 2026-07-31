"""Sending, and recording that we sent.

Every channel funnels through `deliver()`, which writes the `Message` row
first and then hands the body to a backend. That order matters: a provider
that accepts a message and then times out on the response must not leave the
school with a bill and no record.

Announcements fan out in two passes — an in-app row for everybody in the
audience (free, always), then the paid channels for whoever has an address on
that channel. A parent with no email is simply not emailed; they are not an
error.
"""

import logging
from collections.abc import Callable

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.accounts.models import Device
from apps.audit.models import AuditAction
from apps.audit.services import record
from apps.tenants.db import tenant_atomic

from .models import Announcement, Channel, Message, MessageStatus, MessageTemplate
from .push import get_backend as get_push_backend
from .selectors import recipients_for
from .sms import get_backend as get_sms_backend

logger = logging.getLogger(__name__)

#: Channels a school is billed for. In-app is not one of them.
PAID_CHANNELS = {Channel.SMS, Channel.EMAIL}


def render(template: MessageTemplate | str, context: dict) -> str:
    """`str.format_map` with a forgiving map.

    A template written by a school secretary will one day name a placeholder
    that does not exist. Leaving `{whatever}` in the body is a typo; raising
    `KeyError` in the middle of a nine-hundred-parent broadcast is an outage.
    """
    body = template.body if isinstance(template, MessageTemplate) else template
    return body.format_map(_Forgiving(context))


class _Forgiving(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# ---------------------------------------------------------------------------
# One message
# ---------------------------------------------------------------------------
def deliver(
    *,
    channel: str,
    destination: str,
    body: str,
    subject: str = "",
    user=None,
    announcement=None,
    template=None,
    tenant=None,
    route: str = "transactional",
) -> Message:
    """Write the row, then send. Never raises for a provider failure."""
    message = Message.objects.create(
        channel=channel,
        destination=destination,
        subject=subject,
        body=body,
        user=user,
        announcement=announcement,
        template=template,
    )
    if channel == Channel.IN_APP:
        message.status = MessageStatus.DELIVERED
        message.delivered_at = timezone.now()
        message.save(update_fields=["status", "delivered_at", "updated_at"])
        return message

    senders: dict[str, Callable] = {
        Channel.SMS: _send_sms,
        Channel.EMAIL: _send_email,
        Channel.PUSH: _send_push,
    }
    sender = senders[channel]
    try:
        ok, provider, message_id, error = sender(message, tenant=tenant, route=route)
    except Exception as exc:  # a broken backend must not lose the batch
        logger.exception("message delivery raised", extra={"channel": channel})
        ok, provider, message_id, error = False, "", "", exc.__class__.__name__

    message.status = MessageStatus.SENT if ok else MessageStatus.FAILED
    message.provider = provider
    message.provider_message_id = message_id
    message.error = (error or "")[:200]
    message.sent_at = timezone.now() if ok else None
    message.save(
        update_fields=[
            "status",
            "provider",
            "provider_message_id",
            "error",
            "sent_at",
            "updated_at",
        ]
    )
    return message


def _send_sms(message: Message, *, tenant=None, route: str = "transactional"):
    configuration = getattr(tenant, "configuration", None)
    backend = get_sms_backend(
        (configuration.sms_backend or None) if configuration else None,
        sender_id=(configuration.sms_sender_id if configuration else "") or "SCHAPP",
        credentials=(configuration.sms_credentials if configuration else "") or "",
    )
    result = backend.send(message.destination, message.body, route=route)
    return result.ok, result.provider, result.message_id, result.error


def _send_email(message: Message, *, tenant=None, route: str = ""):
    sent = send_mail(
        subject=message.subject or getattr(tenant, "name", "SCHAPP"),
        message=message.body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[message.destination],
        fail_silently=True,
    )
    return bool(sent), "django", "", "" if sent else "The mail server rejected it."


def _send_push(message: Message, *, tenant=None, route: str = ""):
    result = get_push_backend().send(
        message.destination,
        message.subject or getattr(tenant, "name", "SCHAPP"),
        message.body,
        data={"announcement": str(message.announcement_id or "")},
    )
    if result.invalid_token:
        # The device is gone. Clearing the token is what stops us paying to
        # push at it every week until somebody notices.
        Device.objects.filter(push_token=message.destination).update(push_token="")
    return result.ok, result.provider, result.message_id, result.error


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------
@tenant_atomic()
def publish_announcement(
    announcement: Announcement, *, actor=None, request=None, tenant=None
) -> dict:
    """Resolve the audience, write everyone an in-app copy, then broadcast.

    Idempotent: a second publish of the same notice returns the counts it
    already has rather than telling nine hundred parents twice.
    """
    if announcement.is_published:
        return {"announcement": str(announcement.pk), "recipients": announcement.recipients_count}

    users = list(recipients_for(announcement))
    channels = [c for c in announcement.channels or [] if c in PAID_CHANNELS or c == Channel.PUSH]

    counts = dict.fromkeys([Channel.IN_APP, *channels], 0)
    for user in users:
        deliver(
            channel=Channel.IN_APP,
            destination=str(user.pk),
            subject=announcement.title,
            body=announcement.body,
            user=user,
            announcement=announcement,
        )
        counts[Channel.IN_APP] += 1

        for channel in channels:
            for destination in _addresses(user, channel):
                deliver(
                    channel=channel,
                    destination=destination,
                    subject=announcement.title,
                    body=f"{announcement.title}\n\n{announcement.body}",
                    user=user,
                    announcement=announcement,
                    tenant=tenant,
                    # A notice is not an OTP: promotional route, so a school is
                    # not billed the transactional rate for a holiday reminder.
                    route="promotional",
                )
                counts[channel] += 1

    announcement.published_at = timezone.now()
    announcement.recipients_count = len(users)
    announcement.save(update_fields=["published_at", "recipients_count", "updated_at"])
    record(
        AuditAction.ANNOUNCEMENT_PUBLISHED,
        request=request,
        actor=actor,
        obj=announcement,
        summary=f"{announcement.title} → {len(users)} recipients",
    )
    return {"announcement": str(announcement.pk), "recipients": len(users), "sent": counts}


def _addresses(user, channel: str) -> list[str]:
    if channel == Channel.SMS:
        return [user.phone] if user.phone else []
    if channel == Channel.EMAIL:
        return [user.email] if user.email else []
    return list(
        Device.objects.filter(user=user, revoked_at__isnull=True)
        .exclude(push_token="")
        .values_list("push_token", flat=True)
    )


# ---------------------------------------------------------------------------
# Ad-hoc sending
# ---------------------------------------------------------------------------
def send_bulk_sms(*, phones: list[str], body: str, tenant=None, actor=None, request=None) -> dict:
    """A bursar texting the forty parents who owe fees.

    Deliberately not an announcement: it has no audience rule, no in-app copy
    and no notice to read later. It is a text message, and it is logged.
    """
    sent = failed = 0
    for phone in dict.fromkeys(phones):  # de-duplicate, keep order
        message = deliver(
            channel=Channel.SMS, destination=phone, body=body, tenant=tenant, route="promotional"
        )
        sent += message.status == MessageStatus.SENT
        failed += message.status == MessageStatus.FAILED
    record(
        AuditAction.SMS_SENT,
        request=request,
        actor=actor,
        summary=f"{sent} sent, {failed} failed",
    )
    return {"sent": sent, "failed": failed}


# ---------------------------------------------------------------------------
# Delivery reports
# ---------------------------------------------------------------------------
#: Provider wording varies; these are the states that mean "it landed".
DELIVERED_STATES = {"delivered", "delivery_ok", "success", "completed"}

#: …and these mean it never will. Everything a provider says that is in
#: neither set is an interim report — Termii alone sends `Sent`, `Pending` and
#: `Accepted` on the way to a delivery, and treating those as failures made
#: "we sent 412, 9 failed" — the one number this table exists to answer — a
#: count of how chatty the provider was.
FAILED_STATES = {
    "failed",
    "rejected",
    "expired",
    "undeliverable",
    "undelivered",
    "dnd",  # the recipient is on the Nigerian do-not-disturb list
    "blacklisted",
    "insufficient_balance",
}


def apply_delivery_report(*, provider_message_id: str, status: str) -> Message | None:
    """Update one message from a provider's delivery-report webhook.

    Only a report that is recognisably final moves `status`. An unrecognised
    word is recorded in `delivery_status` and otherwise left alone: a provider
    inventing a state must not be able to turn a delivered message into a
    failed one, and reports arrive out of order.
    """
    message = Message.objects.filter(provider_message_id=provider_message_id).first()
    if message is None:
        return None

    word = status.strip().lower()
    message.delivery_status = status[:30]
    if word in DELIVERED_STATES:
        message.status = MessageStatus.DELIVERED
        message.delivered_at = timezone.now()
    elif word in FAILED_STATES and message.status != MessageStatus.DELIVERED:
        # Delivered is terminal: a late failure report for a message the
        # provider already confirmed is the provider contradicting itself.
        message.status = MessageStatus.FAILED

    message.save(update_fields=["delivery_status", "status", "delivered_at", "updated_at"])
    return message
