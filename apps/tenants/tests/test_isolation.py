"""The load-bearing test of the whole product.

If this ever fails, one school can see another school's data.
"""

import pytest
from django.contrib.auth import get_user_model

from apps.tenants import db
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

User = get_user_model()

BURSAR = "+2348031234567"
OTHER = "+2348031234568"


def test_rows_written_in_one_tenant_are_invisible_in_another(make_tenant, ncc_table):
    a = make_tenant("alpha-college")
    b = make_tenant("beta-poly", institution_type="TERTIARY")

    with schema_context(a.schema_name):
        User.objects.create_user(BURSAR, email="bursar@alpha.ng")
        assert User.objects.count() == 1

    with schema_context(b.schema_name):
        assert User.objects.count() == 0
        assert not User.objects.filter(phone=BURSAR).exists()

    with schema_context(a.schema_name):
        assert User.objects.filter(phone=BURSAR).exists()


def test_the_platform_database_cannot_see_tenant_rows(make_tenant, ncc_table):
    a = make_tenant("gamma-school")
    with schema_context(a.schema_name):
        User.objects.create_user(BURSAR)

    db.set_public()
    assert not User.objects.filter(phone=BURSAR).exists()


def test_celery_task_body_runs_in_the_tenant_it_was_given(make_tenant, ncc_table):
    """Background jobs are the classic isolation hole: no request, no header."""
    a = make_tenant("delta-school")
    b = make_tenant("epsilon-school")

    with schema_context(a.schema_name):
        User.objects.create_user(BURSAR)

    def fake_task(schema_name):
        with schema_context(schema_name):
            return list(User.objects.values_list("phone", flat=True))

    assert fake_task(a.schema_name) == [BURSAR]
    assert fake_task(b.schema_name) == []


def test_a_tenant_write_never_lands_in_the_platform_database(make_tenant, ncc_table):
    """The router decides where a row goes; a tenant write must not reach `default`."""
    a = make_tenant("theta-school")
    with schema_context(a.schema_name):
        User.objects.create_user(OTHER)

    db.set_public()
    assert User.objects.using("default").filter(phone=OTHER).count() == 0


def test_each_tenant_gets_its_own_configuration_row(make_tenant):
    a = make_tenant("zeta-school")
    b = make_tenant("eta-school")

    a.configuration.primary_color = "#123456"
    a.configuration.save()
    b.refresh_from_db()

    assert b.configuration.primary_color != "#123456"
