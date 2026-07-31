"""The demo seeder's calendar follows the institution type."""

import pytest

from apps.academics.models import Term
from apps.tenants.db import schema_context
from apps.tenants.management.commands.seed_demo import Command
from apps.tenants.models import InstitutionType

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.mark.parametrize(
    "institution_type,expected",
    [
        (InstitutionType.SECONDARY, ["First Term", "Second Term", "Third Term"]),
        (InstitutionType.TERTIARY, ["First Semester", "Second Semester"]),
    ],
)
def test_the_seeded_calendar_matches_the_institution(
    make_tenant, ncc_table, institution_type, expected
):
    tenant = make_tenant(f"demo-{institution_type.lower()}", institution_type=institution_type)
    with schema_context(tenant.schema_name):
        Command()._calendar(institution_type)
        assert list(Term.objects.order_by("index").values_list("name", flat=True)) == expected
