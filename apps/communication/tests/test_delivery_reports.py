"""A provider's interim report is not a failure.

`apply_delivery_report` treated every status it did not recognise as
"delivered" as FAILED. Nigerian providers report progress — Termii sends
`Sent`, `Pending` and `Accepted` on the way to a delivery — so the first
interim report flipped a healthy message to FAILED, and "we sent 412, 9
failed" (the one number `apps.communication.models` says this table exists to
answer) counted how talkative the provider was.
"""

import pytest

from apps.communication.models import Channel, Message, MessageStatus
from apps.communication.services import apply_delivery_report
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def message(school):
    with schema_context(school.schema_name):
        yield Message.objects.create(
            channel=Channel.SMS,
            destination="+2348031234567",
            body="Fees are due.",
            status=MessageStatus.SENT,
            provider_message_id="prov-1",
        )


def _report(school, status, message_id="prov-1"):
    with schema_context(school.schema_name):
        return apply_delivery_report(provider_message_id=message_id, status=status)


@pytest.mark.parametrize("word", ["Sent", "pending", "Accepted", "in_progress", "whatever"])
def test_an_interim_report_leaves_the_status_alone(school, message, word):
    updated = _report(school, word)
    assert updated.status == MessageStatus.SENT
    # The provider's own word is still recorded — it just does not decide.
    assert updated.delivery_status == word


@pytest.mark.parametrize("word", ["Delivered", "SUCCESS", "completed"])
def test_a_delivery_report_lands(school, message, word):
    updated = _report(school, word)
    assert updated.status == MessageStatus.DELIVERED
    assert updated.delivered_at is not None


@pytest.mark.parametrize("word", ["Failed", "REJECTED", "expired", "dnd"])
def test_a_final_failure_is_recorded_as_one(school, message, word):
    assert _report(school, word).status == MessageStatus.FAILED


def test_delivered_is_terminal(school, message):
    """Reports arrive out of order. A late failure for a message the provider
    already confirmed is the provider contradicting itself, not news."""
    assert _report(school, "delivered").status == MessageStatus.DELIVERED
    assert _report(school, "failed").status == MessageStatus.DELIVERED


def test_an_unknown_message_id_is_not_an_error(school, message):
    """Providers retry non-2xx, and there is nothing to retry towards."""
    assert _report(school, "delivered", message_id="never-seen") is None
