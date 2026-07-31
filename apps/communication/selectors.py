"""Who gets told, and who may read what was said.

Resolving an audience is the part schools get wrong by hand: "JSS2 parents"
means the guardians linked to students enrolled in JSS2 *this session*, not
everyone who was ever a JSS2 parent, and not the pupils themselves.
"""

from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.models import User

from .models import Announcement, Message


def recipients_for(announcement: Announcement) -> QuerySet[User]:
    """The users an announcement is addressed to.

    Narrowed in two independent steps — by role, then by class — because a
    notice is usually "all parents" or "SSS3", and occasionally both.
    """
    users = User.objects.filter(is_active=True)

    roles = announcement.audience_roles or []
    if roles:
        users = users.filter(roles__code__in=roles)

    level_id, arm_id = announcement.level_id, announcement.class_arm_id
    if level_id or arm_id:
        student_filter = Q()
        if arm_id:
            student_filter &= Q(current_arm_id=arm_id)
        if level_id:
            student_filter &= Q(current_level_id=level_id)
        # The student themselves, or a guardian of that student.
        students = _students(student_filter)
        users = users.filter(
            Q(student_profile__in=students) | Q(guardian_profile__ward_links__student__in=students)
        )

    return users.distinct()


def _students(condition: Q):
    from apps.people.models import Student

    return Student.objects.alive().filter(condition)


def announcements_visible_to(user) -> QuerySet[Announcement]:
    """Published, unexpired, and addressed to a role this user holds."""
    now = timezone.now()
    queryset = Announcement.objects.filter(published_at__lte=now).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=now)
    )
    if not (user and user.is_authenticated):
        return queryset.none()
    if user.has_perm_code("communication.manage_announcement"):
        return Announcement.objects.all()

    # An empty audience means the whole school; otherwise one of my roles must
    # be named. The class narrowing was already applied when messages were
    # fanned out, so it is not re-applied on read.
    #
    # ponytail: the role match is done in Python. A JSON array `overlap` lookup
    # is Postgres-only and `contains` is unsupported on SQLite, so a portable
    # query would be an OR of raw JSON matches. A school has tens of live
    # notices; two queries beats a database-specific one. Move it into SQL if a
    # tenant ever has thousands.
    codes = set(user.roles.values_list("code", flat=True))
    matching = [
        announcement.pk
        for announcement in queryset.only("id", "audience_roles")
        if not announcement.audience_roles or codes.intersection(announcement.audience_roles)
    ]
    return queryset.filter(pk__in=matching)


def inbox(user) -> QuerySet[Message]:
    """The in-app messages addressed to this user, newest first."""
    from .models import Channel

    return Message.objects.filter(user=user, channel=Channel.IN_APP).select_related("announcement")
