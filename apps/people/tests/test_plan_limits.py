"""`Plan.max_students` and `Plan.max_staff` are enforced, not decorative.

Both are serialised to the pricing screen by `apps.tenants.serializers`, so a
school is shown a seat count as a promise. Until this check existed the API
took every record regardless — the `Plan` docstring's claim that limits are
enforced at the service layer was the whole enforcement.
"""

import pytest

from apps.people.models import Staff, StaffStatus, Student, StudentStatus
from apps.people.services import PlanLimitReached, create_staff, create_student
from apps.tenants.db import schema_context
from apps.tenants.models import Plan

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def _on_plan(tenant, **limits):
    plan = Plan.objects.create(code="starter", name="Starter", **limits)
    tenant.plan = plan
    tenant.save(update_fields=["plan", "updated_at"])
    return tenant


def test_the_seat_after_the_last_one_is_refused(school):
    _on_plan(school, max_students=2)
    with schema_context(school.schema_name):
        create_student(tenant=school, first_name="Ada", last_name="Obi")
        create_student(tenant=school, first_name="Bola", last_name="Ade")

        with pytest.raises(PlanLimitReached) as caught:
            create_student(tenant=school, first_name="Chidi", last_name="Eze")

    assert caught.value.details == {"limit": 2, "in_use": 2, "plan": "starter"}
    assert caught.value.status_code == 402


def test_a_graduate_frees_the_seat_they_occupied(school):
    """Ten years of alumni must not fill a school's plan.

    Counting every row ever created would make any limit meaningless within a
    few sessions, and the school would be paying for people who left.
    """
    _on_plan(school, max_students=1)
    with schema_context(school.schema_name):
        first = create_student(tenant=school, first_name="Ada", last_name="Obi")
        with pytest.raises(PlanLimitReached):
            create_student(tenant=school, first_name="Bola", last_name="Ade")

        first.status = StudentStatus.GRADUATED
        first.save(update_fields=["status", "updated_at"])

        # The seat is free again.
        create_student(tenant=school, first_name="Bola", last_name="Ade")
        assert Student.objects.count() == 2


def test_a_soft_deleted_record_frees_its_seat(school):
    _on_plan(school, max_students=1)
    with schema_context(school.schema_name):
        first = create_student(tenant=school, first_name="Ada", last_name="Obi")
        first.soft_delete()
        create_student(tenant=school, first_name="Bola", last_name="Ade")


def test_staff_have_their_own_limit(school):
    _on_plan(school, max_students=500, max_staff=1)
    with schema_context(school.schema_name):
        exited = create_staff(tenant=school, first_name="Ngozi", last_name="Okeke")
        with pytest.raises(PlanLimitReached):
            create_staff(tenant=school, first_name="Musa", last_name="Bello")

        exited.status = StaffStatus.EXITED
        exited.save(update_fields=["status", "updated_at"])
        create_staff(tenant=school, first_name="Musa", last_name="Bello")
        assert Staff.objects.count() == 2


def test_a_null_limit_is_unlimited(school):
    """`max_students=None` is the documented "unlimited", not "zero seats"."""
    _on_plan(school, max_students=None, max_staff=None)
    with schema_context(school.schema_name):
        for index in range(3):
            create_student(tenant=school, first_name=f"P{index}", last_name="Obi")
        assert Student.objects.count() == 3


def test_no_plan_at_all_is_not_a_zero_limit(school):
    """A tenant with no plan yet — every one of them between signup and
    billing — must not be locked out of its own first student."""
    assert school.plan is None
    with schema_context(school.schema_name):
        create_student(tenant=school, first_name="Ada", last_name="Obi")


def test_the_csv_import_obeys_the_limit_too(client, school):
    """The reason the check is in the service and not the serializer: an
    import of four hundred pupils bypasses every per-request validator."""
    from apps.people.imports import import_students

    _on_plan(school, max_students=1)
    csv = "first_name,last_name\nAda,Obi\nBola,Ade\nChidi,Eze\n"
    with schema_context(school.schema_name):
        outcome = import_students(csv.encode(), tenant=school)
        assert Student.objects.count() == 0  # the batch is one transaction
    assert any(e.code == "PLAN_LIMIT_REACHED" for e in outcome.errors)
