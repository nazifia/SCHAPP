"""The class-placement endpoints, over HTTP.

The service they call is covered in `test_class_management`. What is checked
here is that something actually reaches it — a rule enforced by a service no
route calls is the same as no rule, and `EnrolmentViewSet` proved that once
already by routing `perform_create` through `enrol_student` and leaving PATCH
writing `class_arm` straight to the column.
"""

import json

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment
from apps.academics.services import enrol_student
from apps.accounts.models import Role, User
from apps.people.models import Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

ARMS = "/api/v1/academics/arms/"
ENROLMENTS = "/api/v1/academics/enrolments/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def registrar(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348034444444", first_name="Ada")
        user.roles.add(Role.objects.get(code="school_admin"))
        yield user


@pytest.fixture
def headers(school, registrar):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(registrar, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


@pytest.fixture
def world(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm = ClassArm.objects.create(level=level, name="Gold", capacity=2)
        enrolments = [
            enrol_student(
                student=Student.objects.create(
                    admission_number=f"KC/25/{n:04d}", first_name=f"Pupil{n}", last_name="Test"
                ),
                session=session,
                level=level,
            )
            for n in (1, 2, 3)
        ]
        yield {"session": session, "level": level, "arm": arm, "enrolments": enrolments}


def _post(client, url, body, headers):
    return client.post(url, data=json.dumps(body), content_type="application/json", **headers)


def test_the_worklist_lists_everyone_without_a_class(client, world, headers):
    body = client.get(f"{ENROLMENTS}unplaced/", **headers).json()
    assert len(body) == 3


def test_allocating_a_batch_seats_them_and_empties_the_worklist(client, school, world, headers):
    batch = [str(e.pk) for e in world["enrolments"][:2]]
    response = _post(client, f"{ARMS}{world['arm'].pk}/allocate/", {"enrolments": batch}, headers)

    assert response.status_code == 200
    assert sorted(row["roll_number"] for row in response.json()) == [1, 2]
    assert len(client.get(f"{ENROLMENTS}unplaced/", **headers).json()) == 1

    with schema_context(school.schema_name):
        student = Enrolment.objects.get(pk=batch[0]).student
        assert student.current_arm_id == world["arm"].pk


def test_a_batch_over_capacity_is_refused_whole(client, school, world, headers):
    batch = [str(e.pk) for e in world["enrolments"]]
    response = _post(client, f"{ARMS}{world['arm'].pk}/allocate/", {"enrolments": batch}, headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ARM_FULL"
    with schema_context(school.schema_name):
        assert Enrolment.objects.filter(class_arm__isnull=False).count() == 0


def test_patching_a_class_goes_through_the_rules(client, school, world, headers):
    """Was a bare column write: past capacity, and `current_arm` left stale."""
    for enrolment in world["enrolments"][:2]:
        _post(
            client,
            f"{ARMS}{world['arm'].pk}/allocate/",
            {"enrolments": [str(enrolment.pk)]},
            headers,
        )

    third = world["enrolments"][2]
    response = client.patch(
        f"{ENROLMENTS}{third.pk}/",
        data=json.dumps({"class_arm": str(world["arm"].pk)}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ARM_FULL"
    with schema_context(school.schema_name):
        third.refresh_from_db()
        assert third.class_arm_id is None


def test_a_successful_patch_syncs_the_student(client, school, world, headers):
    target = world["enrolments"][0]
    response = client.patch(
        f"{ENROLMENTS}{target.pk}/",
        data=json.dumps({"class_arm": str(world["arm"].pk)}),
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    with schema_context(school.schema_name):
        target.refresh_from_db()
        assert target.class_arm_id == world["arm"].pk
        assert target.roll_number == 1
        target.student.refresh_from_db()
        assert target.student.current_arm_id == world["arm"].pk


def test_the_arm_list_carries_seats_left(client, world, headers):
    _post(
        client,
        f"{ARMS}{world['arm'].pk}/allocate/",
        {"enrolments": [str(world["enrolments"][0].pk)]},
        headers,
    )
    body = client.get(f"{ARMS}{world['arm'].pk}/", **headers).json()
    assert body["enrolled"] == 1
    assert body["seats_left"] == 1
