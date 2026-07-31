"""Detaching a guardian.

The link is a parent's route into a child's results and invoices, so removing
it is an access change, not a tidy-up: it must take effect immediately, must
refuse anyone without `people.manage_student`, and must leave a trail.
"""

import pytest

from apps.accounts.models import User
from apps.accounts.services import assign_role
from apps.audit.models import AuditAction, AuditLog
from apps.auth_phone import tokens
from apps.people.models import Student, StudentGuardian
from apps.people.selectors import wards_of
from apps.people.services import link_guardian
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("queens-college")


@pytest.fixture
def world(school):
    """One child, one parent who can see her, one registrar who may unlink."""
    with schema_context(school.schema_name):
        pupil = Student.objects.create(
            admission_number="QC/25/0001", first_name="Ada", last_name="Obi"
        )
        guardian_user = User.objects.create_user("+2348032222222", first_name="Parent")
        assign_role(guardian_user, "guardian")
        link = link_guardian(student=pupil, phone="+2348032222222", first_name="Parent")
        link.guardian.user = guardian_user
        link.guardian.save()

        registrar = User.objects.create_user("+2348033333333", first_name="Registrar")
        assign_role(registrar, "registrar")

        yield {"pupil": pupil, "link": link, "guardian_user": guardian_user, "registrar": registrar}


def _auth(school, user):
    with schema_context(school.schema_name):
        return tokens.issue_for_user(user, tenant=school)["access"]


def _unlink(client, school, user, pupil, link_id):
    return client.delete(
        f"/api/v1/people/students/{pupil.pk}/guardians/{link_id}/",
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {_auth(school, user)}",
    )


def test_unlinking_ends_the_guardians_view_of_the_child(client, school, world):
    pupil, link = world["pupil"], world["link"]

    response = _unlink(client, school, world["registrar"], pupil, link.pk)

    assert response.status_code == 204
    with schema_context(school.schema_name):
        assert not StudentGuardian.objects.filter(pk=link.pk).exists()
        assert list(wards_of(world["guardian_user"])) == []
        entry = AuditLog.objects.filter(action=AuditAction.GUARDIAN_UNLINKED).first()
        assert entry is not None
        assert "Ada Obi" in entry.summary


def test_a_guardian_cannot_unlink_themselves_from_a_child(client, school, world):
    """Or from anyone else's: the permission is the office's, not the parent's."""
    pupil, link = world["pupil"], world["link"]

    response = _unlink(client, school, world["guardian_user"], pupil, link.pk)

    assert response.status_code == 403
    with schema_context(school.schema_name):
        assert StudentGuardian.objects.filter(pk=link.pk).exists()


def test_a_link_belonging_to_another_child_is_a_404_not_a_delete(client, school, world):
    """The link id alone must not be enough — it is checked against the child
    in the path, or one student's id would delete another's parent."""
    with schema_context(school.schema_name):
        other = Student.objects.create(
            admission_number="QC/25/0002", first_name="Bola", last_name="Ade"
        )

    response = _unlink(client, school, world["registrar"], other, world["link"].pk)

    assert response.status_code == 404
    with schema_context(school.schema_name):
        assert StudentGuardian.objects.filter(pk=world["link"].pk).exists()
