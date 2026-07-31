"""Starting academic structure for a brand-new school.

Seeded at provisioning so an administrator lands on a usable system rather
than an empty one. Everything is `get_or_create`, so re-running provisioning
never duplicates or overwrites a school's own edits.

No session or term is seeded: those carry real dates only the school knows.
"""

import logging

from .models import ClassLevel, Stream

logger = logging.getLogger(__name__)

SECONDARY_LEVELS = [
    ("JSS1", "JSS 1", 1),
    ("JSS2", "JSS 2", 2),
    ("JSS3", "JSS 3", 3),
    ("SSS1", "SSS 1", 4),
    ("SSS2", "SSS 2", 5),
    ("SSS3", "SSS 3", 6),
]

TERTIARY_LEVELS = [
    ("100", "100 Level", 1),
    ("200", "200 Level", 2),
    ("300", "300 Level", 3),
    ("400", "400 Level", 4),
    ("500", "500 Level", 5),
]

STREAMS = [("science", "Science"), ("arts", "Arts"), ("commercial", "Commercial")]


def seed_structure(institution_type: str) -> int:
    levels = TERTIARY_LEVELS if institution_type == "TERTIARY" else SECONDARY_LEVELS

    created_levels = []
    for code, name, order in levels:
        level, _ = ClassLevel.objects.get_or_create(
            code=code, defaults={"name": name, "order": order}
        )
        created_levels.append(level)

    # Chain the promotion path; the last level graduates instead.
    for current, following in zip(created_levels, created_levels[1:], strict=False):
        if current.next_level_id is None:
            current.next_level = following
            current.save(update_fields=["next_level", "updated_at"])
    last = created_levels[-1]
    if not last.is_terminal:
        last.is_terminal = True
        last.save(update_fields=["is_terminal", "updated_at"])

    if institution_type != "TERTIARY":
        for code, name in STREAMS:
            Stream.objects.get_or_create(code=code, defaults={"name": name})

    return len(created_levels)
