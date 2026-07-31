"""Timetable clash detection.

Uses the real database because clash detection is a query, not arithmetic.
"""

from datetime import time

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Room,
    Subject,
    Term,
    TimetableEntry,
)
from apps.academics.services import TimetableClash, create_timetable_entry, find_clashes
from apps.people.models import Staff
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def fixtures(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        term = Term.objects.create(
            session=session,
            index=1,
            name="First Term",
            start_date="2025-09-01",
            end_date="2025-12-15",
            is_current=True,
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm_a = ClassArm.objects.create(level=level, name="A")
        arm_b = ClassArm.objects.create(level=level, name="B")
        maths = Subject.objects.create(code="MTH", title="Mathematics")
        english = Subject.objects.create(code="ENG", title="English")
        teacher = Staff.objects.create(
            staff_number="STF/25/0001", first_name="Amaka", last_name="Obi"
        )
        other = Staff.objects.create(
            staff_number="STF/25/0002", first_name="Tunde", last_name="Bello"
        )
        room = Room.objects.create(code="R1")
        yield {
            "term": term,
            "level": level,
            "arm_a": arm_a,
            "arm_b": arm_b,
            "maths": maths,
            "english": english,
            "teacher": teacher,
            "other": other,
            "room": room,
        }


def _entry(f, **over):
    base = {
        "term": f["term"],
        "day": 1,
        "start_time": time(9, 0),
        "end_time": time(10, 0),
        "subject": f["maths"],
        "staff": f["teacher"],
        "class_arm": f["arm_a"],
        "room": f["room"],
    }
    return {**base, **over}


def test_a_clean_period_saves(school, fixtures):
    with schema_context(school.schema_name):
        entry = create_timetable_entry(**_entry(fixtures))
        assert TimetableEntry.objects.count() == 1
        assert entry.pk


def test_the_same_teacher_cannot_be_in_two_places(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        with pytest.raises(TimetableClash) as exc:
            create_timetable_entry(
                **_entry(
                    fixtures, class_arm=fixtures["arm_b"], room=None, subject=fixtures["english"]
                )
            )
        assert exc.value.details["clashes"][0]["reason"] == "teacher"


def test_a_room_cannot_hold_two_classes(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        with pytest.raises(TimetableClash) as exc:
            create_timetable_entry(
                **_entry(fixtures, staff=fixtures["other"], class_arm=fixtures["arm_b"])
            )
        assert exc.value.details["clashes"][0]["reason"] == "room"


def test_a_class_cannot_have_two_lessons_at_once(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        with pytest.raises(TimetableClash) as exc:
            create_timetable_entry(
                **_entry(fixtures, staff=fixtures["other"], room=None, subject=fixtures["english"])
            )
        assert exc.value.details["clashes"][0]["reason"] == "class"


def test_touching_periods_do_not_clash(school, fixtures):
    """10:00-11:00 after 09:00-10:00 is back-to-back, not a collision."""
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        create_timetable_entry(**_entry(fixtures, start_time=time(10, 0), end_time=time(11, 0)))
        assert TimetableEntry.objects.count() == 2


def test_partial_overlap_clashes(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        with pytest.raises(TimetableClash):
            create_timetable_entry(
                **_entry(fixtures, start_time=time(9, 30), end_time=time(10, 30))
            )


def test_a_period_wholly_inside_another_clashes(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        with pytest.raises(TimetableClash):
            create_timetable_entry(**_entry(fixtures, start_time=time(9, 15), end_time=time(9, 45)))


def test_different_days_never_clash(school, fixtures):
    with schema_context(school.schema_name):
        create_timetable_entry(**_entry(fixtures))
        create_timetable_entry(**_entry(fixtures, day=2))
        assert TimetableEntry.objects.count() == 2


def test_editing_an_entry_does_not_clash_with_itself(school, fixtures):
    with schema_context(school.schema_name):
        entry = create_timetable_entry(**_entry(fixtures))
        assert find_clashes(entry) == []


def test_a_period_must_end_after_it_starts(school, fixtures):
    from apps.academics.services import InvalidPeriod

    with schema_context(school.schema_name), pytest.raises(InvalidPeriod) as exc:
        create_timetable_entry(**_entry(fixtures, start_time=time(11, 0), end_time=time(10, 0)))
    assert exc.value.code == "INVALID_PERIOD"


def test_the_api_refuses_a_period_that_ends_before_it_starts(client, school, fixtures):
    """The viewset used to check clashes and call `serializer.save()`, which
    does not run `Model.clean()` — so this was accepted over HTTP while the
    service refused it."""
    from apps.accounts.models import Role, User
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Head")
        user.roles.add(Role.objects.get(code="principal"))
        pair = tokens.issue_for_user(user, tenant=school)
        payload = {
            "term": str(fixtures["term"].pk),
            "day": 1,
            "start_time": "11:00",
            "end_time": "10:00",
            "subject": str(fixtures["maths"].pk),
            "staff": str(fixtures["teacher"].pk),
            "class_arm": str(fixtures["arm_a"].pk),
        }

    response = client.post(
        "/api/v1/academics/timetable/",
        payload,
        content_type="application/json",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )

    assert response.status_code == 400
    with schema_context(school.schema_name):
        assert TimetableEntry.objects.count() == 0
