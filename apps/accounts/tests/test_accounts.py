import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import Role, User
from apps.accounts.services import assign_role, seed_roles
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def test_provisioning_seeds_the_system_roles(school):
    with schema_context(school.schema_name):
        assert Role.objects.filter(is_system=True).count() >= 13
        assert Role.objects.get(code="teacher").permissions


def test_seeding_twice_creates_nothing_extra(school):
    with schema_context(school.schema_name):
        before = Role.objects.count()
        seed_roles()
        assert Role.objects.count() == before


def test_user_is_created_from_any_dialling_form(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("0803 123 4567")
        assert user.phone == "+2348031234567"
        assert user.phone_display == "0803 123 4567"


def test_an_invalid_number_cannot_become_a_user(school):
    from apps.numbering.msisdn import InvalidMsisdn

    with schema_context(school.schema_name), pytest.raises(InvalidMsisdn):
        User.objects.create_user("0803")


def test_permissions_come_from_roles(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567")
        assert not user.has_perm_code("assessment.enter_score")

        assign_role(user, "teacher")
        assert user.has_perm_code("assessment.enter_score")
        assert not user.has_perm_code("assessment.publish_results")


def test_several_roles_on_one_user_union_their_permissions(school):
    """A bursar who also teaches holds both sets, not the last one assigned."""
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567")
        assign_role(user, "teacher")
        assign_role(user, "bursar")

        assert sorted(user.roles.values_list("code", flat=True)) == ["bursar", "teacher"]
        assert user.has_perm_code("assessment.enter_score")  # from teacher
        assert user.has_perm_code("finance.record_payment")  # from bursar


def test_the_owner_wildcard_grants_everything(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567")
        assign_role(user, "school_admin")
        assert user.has_perm_code("finance.manage_payroll")
        assert user.has_perm_code("anything.at.all")


def test_role_rejects_unknown_permission_codes(school):
    with schema_context(school.schema_name):
        role = Role(code="made-up", name="Made up", permissions=["not.a.real.permission"])
        with pytest.raises(ValidationError):
            role.clean()


def test_nin_is_encrypted_at_rest_and_masked_in_python(school):
    from django.db import connections

    from apps.tenants import db

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567", nin="12345678912")
        assert user.masked_nin == "•••••••8912"

        # Looked up by phone, not by id: neither SQLite nor MySQL has a UUID
        # type, so a dashed `str(uuid)` matches nothing in the stored char(32).
        with connections[db.current_alias()].cursor() as cursor:
            cursor.execute("SELECT nin FROM accounts_user WHERE phone = %s", [user.phone])
            stored = cursor.fetchone()[0]
        assert stored.startswith("enc:")
        assert "12345678912" not in stored


def test_pin_rules(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031234567")

        for weak in ("123456", "000000", "111111", "12345", "abcdef"):
            with pytest.raises(ValidationError):
                user.set_pin(weak)

        user.set_pin("729401")
        user.save()
        assert user.check_pin("729401")
        assert not user.check_pin("729402")
        assert "729401" not in user.pin_hash


def test_the_same_phone_can_exist_in_two_schools(make_tenant, ncc_table):
    """A parent with children in two schools is normal, not a conflict."""
    a = make_tenant("school-a")
    b = make_tenant("school-b")

    with schema_context(a.schema_name):
        User.objects.create_user("+2348031234567", first_name="Ngozi")
    with schema_context(b.schema_name):
        User.objects.create_user("+2348031234567", first_name="Ngozi")
        assert User.objects.count() == 1


def test_a_phone_is_unique_within_one_school(school):
    from django.db import IntegrityError

    with schema_context(school.schema_name):
        User.objects.create_user("+2348031234567")
        with pytest.raises(IntegrityError):
            User.objects.create_user("08031234567")
