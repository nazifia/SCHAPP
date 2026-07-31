"""CSV exports actually stream.

A generator-backed `StreamingHttpResponse` does not touch a row until someone
consumes it, so an export that names a field that does not exist looks fine
until an office downloads it. These tests consume the stream.
"""

import pytest

from apps.api.reporting import ExportView
from apps.people.models import Staff, Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


def _rows(response) -> list[str]:
    return b"".join(response.streaming_content).decode().splitlines()


def test_the_student_export_names_fields_that_exist(rf, school):
    with schema_context(school.schema_name):
        Student.objects.create(
            admission_number="KC/25/0001",
            first_name="Ngozi",
            other_name="Amaka",
            last_name="Ali",
        )
        rows = _rows(ExportView().students(rf.get("/")))

    assert rows[0].startswith("Number,Surname,First name,Other names")
    assert rows[1].startswith("KC/25/0001,Ali,Ngozi,Amaka")


def test_the_staff_export_streams(rf, school):
    with schema_context(school.schema_name):
        Staff.objects.create(staff_number="KC/S/001", first_name="Bola", last_name="Ade")
        rows = _rows(ExportView().staff(rf.get("/")))

    assert len(rows) == 2
    assert rows[1].startswith("KC/S/001,Ade,Bola")
