"""Object-level scoping.

Schema isolation stops school A seeing school B. These tests cover the other
half: inside one school, a teacher must not see a class they do not teach and
a guardian must not see a child that is not theirs.
"""

import pytest

from apps.academics.models import (
    AcademicSession,
    ClassArm,
    ClassLevel,
    Subject,
    TeachingAssignment,
)
from apps.accounts.models import User
from apps.accounts.services import assign_role
from apps.people.models import Staff, Student
from apps.people.selectors import can_view_student, students_visible_to, wards_of
from apps.people.services import link_guardian
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def world(school):
    """Two arms, a teacher on one of them, a guardian with one child."""
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm_a = ClassArm.objects.create(level=level, name="A")
        arm_b = ClassArm.objects.create(level=level, name="B")

        pupil_a = Student.objects.create(
            admission_number="KC/25/0001",
            first_name="Ada",
            last_name="A",
            current_level=level,
            current_arm=arm_a,
        )
        pupil_b = Student.objects.create(
            admission_number="KC/25/0002",
            first_name="Bola",
            last_name="B",
            current_level=level,
            current_arm=arm_b,
        )

        teacher_user = User.objects.create_user("+2348031111111", first_name="Teacher")
        assign_role(teacher_user, "teacher")
        teacher = Staff.objects.create(
            staff_number="STF/25/0001", first_name="Teacher", last_name="One", user=teacher_user
        )
        maths = Subject.objects.create(code="MTH", title="Mathematics")
        TeachingAssignment.objects.create(
            staff=teacher, subject=maths, session=session, class_arm=arm_a
        )

        guardian_user = User.objects.create_user("+2348032222222", first_name="Parent")
        assign_role(guardian_user, "guardian")
        link = link_guardian(student=pupil_a, phone="+2348032222222", first_name="Parent")
        link.guardian.user = guardian_user
        link.guardian.save()

        principal = User.objects.create_user("+2348033333333", first_name="Principal")
        assign_role(principal, "principal")

        yield {
            "arm_a": arm_a,
            "arm_b": arm_b,
            "pupil_a": pupil_a,
            "pupil_b": pupil_b,
            "teacher_user": teacher_user,
            "guardian_user": guardian_user,
            "principal": principal,
            "session": session,
            "maths": maths,
            "teacher": teacher,
        }


def test_a_teacher_sees_only_their_own_class(school, world):
    with schema_context(school.schema_name):
        visible = students_visible_to(world["teacher_user"])
        assert list(visible) == [world["pupil_a"]]
        assert not can_view_student(world["teacher_user"], world["pupil_b"])


def test_a_teaching_assignment_without_view_student_grants_nothing(school, world):
    """The assignment is a timetable fact; `people.view_student` is the grant."""
    from apps.accounts.models import Role

    with schema_context(school.schema_name):
        timetabler = Role.objects.create(
            code="timetabler", name="Timetabler", permissions=["academics.manage_timetable"]
        )
        user = world["teacher_user"]
        user.roles.set([timetabler])

        assert students_visible_to(user).count() == 0


def test_a_form_teacher_sees_their_whole_arm_without_a_subject(school, world):
    with schema_context(school.schema_name):
        arm_b = world["arm_b"]
        arm_b.form_teacher = world["teacher"]
        arm_b.save()

        visible = set(students_visible_to(world["teacher_user"]))
        assert visible == {world["pupil_a"], world["pupil_b"]}


def test_a_guardian_sees_only_their_ward(school, world):
    with schema_context(school.schema_name):
        visible = list(students_visible_to(world["guardian_user"]))
        assert visible == [world["pupil_a"]]
        assert list(wards_of(world["guardian_user"])) == [world["pupil_a"]]


def test_a_principal_sees_everyone(school, world):
    with schema_context(school.schema_name):
        assert students_visible_to(world["principal"]).count() == 2


def test_a_user_with_no_role_sees_nothing(school, world):
    with schema_context(school.schema_name):
        nobody = User.objects.create_user("+2348034444444")
        assert students_visible_to(nobody).count() == 0


def test_an_anonymous_user_sees_nothing(school, world):
    from django.contrib.auth.models import AnonymousUser

    with schema_context(school.schema_name):
        assert students_visible_to(AnonymousUser()).count() == 0


def test_a_student_can_always_see_themselves(school, world):
    with schema_context(school.schema_name):
        pupil_user = User.objects.create_user("+2348035555555")
        assign_role(pupil_user, "student")
        pupil = world["pupil_b"]
        pupil.user = pupil_user
        pupil.save()

        assert list(students_visible_to(pupil_user)) == [pupil]


def test_scoping_survives_the_list_endpoint(client, school, world):
    """Filtering in the queryset, not after: counts and cursors leak nothing."""
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        pair = tokens.issue_for_user(world["teacher_user"], tenant=school)

    response = client.get(
        "/api/v1/people/students/",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )
    assert response.status_code == 200
    names = [row["full_name"] for row in response.json()["results"]]
    assert names == ["Ada A"]
