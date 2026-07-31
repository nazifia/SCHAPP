"""`/academics/my-classes/` — what the mark sheet and the register are built on.

Both screens open by asking this endpoint which subjects and classes to offer.
Scoped to a teaching assignment, it answered two empty lists to anyone who
holds no assignment and never will — the proprietor and the office account —
so `/scores` offered nothing to pick and no mark sheet could be reached, while
`ScoreViewSet.sheet` would have served every one of them on the strength of
`assessment.view_all_scores`.
"""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Subject, Term
from apps.accounts.models import Role, User
from apps.people.models import Staff
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

MY_CLASSES = "/api/v1/academics/my-classes/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def _headers(school, user):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def structure(school):
    """Two arms and two subjects; the teacher below is assigned to one of each."""
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
        yield {
            "term": term,
            "arms": [
                ClassArm.objects.create(level=level, name="A"),
                ClassArm.objects.create(level=level, name="B"),
            ],
            "subjects": [
                Subject.objects.create(code="MTH", title="Mathematics"),
                Subject.objects.create(code="ENG", title="English"),
            ],
            "level": level,
        }


@pytest.fixture
def teacher(school, structure):
    with schema_context(school.schema_name):
        from apps.academics.models import TeachingAssignment

        user = User.objects.create_user("+2348032222222", first_name="Sade")
        user.roles.add(Role.objects.get(code="teacher"))
        staff = Staff.objects.create(
            user=user, staff_number="KC/S/001", first_name="Sade", last_name="Bello"
        )
        TeachingAssignment.objects.create(
            staff=staff,
            subject=structure["subjects"][0],
            class_arm=structure["arms"][0],
            session=structure["term"].session,
            term=structure["term"],
        )
        yield user


@pytest.fixture
def officer(school):
    """The proprietor keying a missing mark: every permission, no assignment.

    `school_admin` is the role that actually reaches `/scores` this way — the
    tab and the `sheet` endpoint both want `assessment.enter_score`, and a
    principal or registrar holds `view_all_scores` without it.
    """
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348033333333", first_name="Bode")
        user.roles.add(Role.objects.get(code="school_admin"))
        yield user


def test_a_teacher_gets_only_what_they_teach(client, school, teacher, structure):
    body = client.get(MY_CLASSES, **_headers(school, teacher)).json()

    assert [s["code"] for s in body["subjects"]] == ["MTH"]
    assert [a["name"] for a in body["arms"]] == ["A"]
    assert body["term"]["name"] == "First Term"


def test_an_exams_officer_gets_the_whole_school(client, school, officer, structure):
    """Was two empty lists, which is a mark-entry screen that cannot be used."""
    body = client.get(MY_CLASSES, **_headers(school, officer)).json()

    assert sorted(s["code"] for s in body["subjects"]) == ["ENG", "MTH"]
    assert sorted(a["name"] for a in body["arms"]) == ["A", "B"]


def test_the_sheet_the_officer_is_offered_is_one_they_may_open(client, school, officer, structure):
    """The two halves agree: everything listed here is served by `sheet`."""
    headers = _headers(school, officer)
    body = client.get(MY_CLASSES, **headers).json()

    for subject in body["subjects"]:
        for arm in body["arms"]:
            response = client.get(
                f"/api/v1/assessment/scores/sheet/"
                f"?term={body['term']['id']}&subject={subject['id']}&class_arm={arm['id']}",
                **headers,
            )
            assert response.status_code == 200, (subject["code"], arm["name"])
