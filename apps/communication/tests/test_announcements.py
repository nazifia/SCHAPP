"""Who gets told, once, and on which channels.

The failure modes worth pinning: telling the wrong class, telling everybody
twice, and paying for an SMS the school did not ask for.
"""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel
from apps.accounts.models import Role, User
from apps.communication import selectors, services
from apps.communication.models import Announcement, Channel, Message, MessageStatus
from apps.communication.sms.console import LocMemBackend
from apps.people.models import Guardian, Student, StudentGuardian
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture(autouse=True)
def _clear_outbox():
    LocMemBackend.outbox.clear()
    yield
    LocMemBackend.outbox.clear()


@pytest.fixture
def setup(school):
    with schema_context(school.schema_name):
        AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        jss1 = ClassLevel.objects.get(code="JSS1")
        jss2 = ClassLevel.objects.get(code="JSS2")
        arm = ClassArm.objects.create(level=jss1, name="A")
        guardian_role = Role.objects.get(code="guardian")

        parents = []
        for index, level in enumerate([jss1, jss2], start=1):
            student = Student.objects.create(
                admission_number=f"KC/25/000{index}",
                first_name="Pupil",
                last_name=str(index),
                current_level=level,
                current_arm=arm if level == jss1 else None,
            )
            user = User.objects.create(
                phone=f"+23480300000{index:02d}", first_name="Parent", last_name=str(index)
            )
            user.roles.add(guardian_role)
            guardian = Guardian.objects.create(
                user=user, first_name="Parent", last_name=str(index), phone=user.phone
            )
            StudentGuardian.objects.create(student=student, guardian=guardian, is_primary=True)
            parents.append(user)

        yield {"jss1": jss1, "jss2": jss2, "arm": arm, "parents": parents}


def test_an_announcement_for_one_level_reaches_only_that_level(school, setup):
    with schema_context(school.schema_name):
        announcement = Announcement.objects.create(
            title="JSS1 excursion",
            body="Bring ₦2,000 on Friday.",
            audience_roles=["guardian"],
            level=setup["jss1"],
        )
        recipients = list(selectors.recipients_for(announcement))
        assert recipients == [setup["parents"][0]]


def test_publishing_writes_an_in_app_copy_for_everyone_and_is_idempotent(school, setup):
    with schema_context(school.schema_name):
        announcement = Announcement.objects.create(
            title="Resumption", body="School resumes 8 January.", audience_roles=["guardian"]
        )
        result = services.publish_announcement(announcement)
        assert result["recipients"] == 2
        assert Message.objects.filter(channel=Channel.IN_APP).count() == 2
        # No paid channel was asked for, so no SMS was sent.
        assert not LocMemBackend.outbox

        services.publish_announcement(announcement)
        assert Message.objects.filter(channel=Channel.IN_APP).count() == 2


def test_an_sms_announcement_texts_every_recipient_and_logs_it(school, setup):
    with schema_context(school.schema_name):
        announcement = Announcement.objects.create(
            title="Fees due",
            body="Second term fees are due on Friday.",
            audience_roles=["guardian"],
            channels=[Channel.SMS],
        )
        services.publish_announcement(announcement)

        assert len(LocMemBackend.outbox) == 2
        # A notice goes on the promotional route; only OTP pays for DND.
        assert {entry["route"] for entry in LocMemBackend.outbox} == {"promotional"}
        sent = Message.objects.filter(channel=Channel.SMS)
        assert sent.count() == 2
        assert all(message.status == MessageStatus.SENT for message in sent)


def test_a_delivery_report_marks_the_message_delivered(school, setup):
    with schema_context(school.schema_name):
        announcement = Announcement.objects.create(
            title="Fees due", body="Pay up.", audience_roles=["guardian"], channels=[Channel.SMS]
        )
        services.publish_announcement(announcement)
        message = Message.objects.filter(channel=Channel.SMS).first()

        services.apply_delivery_report(
            provider_message_id=message.provider_message_id, status="delivered"
        )
        message.refresh_from_db()
        assert message.status == MessageStatus.DELIVERED
        assert message.delivered_at is not None

        # A report we cannot match is not an error.
        assert services.apply_delivery_report(provider_message_id="nope", status="failed") is None


def test_a_reader_only_sees_notices_addressed_to_a_role_they_hold(school, setup):
    with schema_context(school.schema_name):
        for_guardians = Announcement.objects.create(
            title="Parents", body="…", audience_roles=["guardian"]
        )
        for_staff = Announcement.objects.create(title="Staff", body="…", audience_roles=["teacher"])
        for_all = Announcement.objects.create(title="Everyone", body="…")
        for announcement in (for_guardians, for_staff, for_all):
            services.publish_announcement(announcement)

        visible = selectors.announcements_visible_to(setup["parents"][0])
        assert {a.title for a in visible} == {"Parents", "Everyone"}


def test_a_forgiving_template_leaves_an_unknown_placeholder_alone():
    assert services.render("Hi {name}, {oops}", {"name": "Ada"}) == "Hi Ada, {oops}"
