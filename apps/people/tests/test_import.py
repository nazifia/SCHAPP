"""Bulk student import from a CSV.

The cases that matter to an office: the file the school actually has (mixed
header spellings, blank cells), a file with one bad row, and a file that would
create the same student twice.
"""

import pytest

from apps.academics.models import AcademicSession, ClassArm, ClassLevel, Enrolment
from apps.people.imports import import_students, read_rows
from apps.people.models import Guardian, Student
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

HEADER = "First Name,Surname,Sex,DOB,Class,Parent Name,Parent Phone\n"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def setup(school):
    with schema_context(school.schema_name):
        session = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        level = ClassLevel.objects.get(code="JSS1")
        arm = ClassArm.objects.create(level=level, name="A", capacity=2)
        yield {"session": session, "level": level, "arm": arm}


def test_a_spreadsheet_with_the_school_s_own_headers_imports(school, setup):
    csv = HEADER + (
        "Chinedu,Okafor,M,2012-04-05,JSS1 A,Ngozi Okafor,08031234567\n"
        "Aisha,Bello,Female,06/11/2011,JSS1 A,,\n"
    )
    with schema_context(school.schema_name):
        outcome = import_students(csv, session=setup["session"], tenant=school)

        assert outcome.ok, outcome.errors
        assert outcome.created == 2
        assert Student.objects.count() == 2

        chinedu = Student.objects.get(first_name="Chinedu")
        assert chinedu.admission_number, "the numbering service still runs"
        assert chinedu.gender == "M"
        assert chinedu.current_arm == setup["arm"]
        # Both spellings of the date column land on the same field.
        assert Student.objects.get(first_name="Aisha").date_of_birth.year == 2011
        assert Enrolment.objects.count() == 2
        assert Guardian.objects.get(phone="+2348031234567").last_name == "Okafor"


def test_one_bad_row_writes_nothing_and_names_its_line(school, setup):
    csv = HEADER + (
        "Chinedu,Okafor,M,2012-04-05,JSS1 A,,\n"
        "Aisha,Bello,F,not-a-date,JSS1 A,,\n"
        "Tunde,Alabi,M,2012-01-01,SSS9 Z,,\n"
    )
    with schema_context(school.schema_name):
        outcome = import_students(csv, session=setup["session"], tenant=school)

        assert not outcome.ok
        assert outcome.created == 0, "a partial import is worse than none"
        assert Student.objects.count() == 0

        by_index = {e.index: e for e in outcome.errors}
        assert by_index[1].code == "BAD_DATE"
        assert by_index[2].code == "UNKNOWN_REFERENCE"
        assert by_index[1].identifier == "Aisha Bello"


def test_a_duplicate_admission_number_is_reported_not_raised(school, setup):
    csv = (
        "first_name,last_name,admission_number\nChinedu,Okafor,KC/25/0001\nAisha,Bello,KC/25/0001\n"
    )
    with schema_context(school.schema_name):
        outcome = import_students(csv, tenant=school)

        # The savepoint per row is what makes this a reported row rather than a
        # broken transaction that takes the rest of the file's errors with it.
        assert [e.code for e in outcome.errors] == ["DUPLICATE"]
        assert Student.objects.count() == 0


def test_a_file_without_the_required_columns_is_refused_whole(school):
    from apps.people.imports import StudentImportError

    with schema_context(school.schema_name), pytest.raises(StudentImportError) as exc:
        import_students("nickname,house\nChidi,Blue\n")
    assert exc.value.code == "MISSING_COLUMNS"


def test_excel_s_byte_order_mark_does_not_hide_the_first_column():
    rows = read_rows("﻿first_name,last_name\nChinedu,Okafor\n".encode())
    assert rows == [{"first_name": "Chinedu", "last_name": "Okafor"}]


def test_the_endpoint_takes_a_file_upload_and_reports_the_bad_rows(client, school, setup):
    """The service is tested above; this is the multipart plumbing and the 422."""
    from io import BytesIO

    from apps.accounts.models import User
    from apps.accounts.services import assign_role
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        registrar = User.objects.create_user("+2348031111111", first_name="Registrar")
        assign_role(registrar, "registrar")
        pair = tokens.issue_for_user(registrar, tenant=school)

    upload = BytesIO(HEADER.encode() + b"Chinedu,Okafor,M,not-a-date,JSS1 A,,\n")
    upload.name = "intake.csv"

    response = client.post(
        "/api/v1/people/students/import/",
        {"file": upload},
        HTTP_X_TENANT_SLUG=school.slug,
        HTTP_AUTHORIZATION=f"Bearer {pair['access']}",
    )

    assert response.status_code == 422
    body = response.json()
    assert body["created"] == 0
    assert body["errors"][0]["index"] == 0
    assert body["errors"][0]["code"] == "BAD_DATE"


def test_capacity_is_enforced_unless_the_office_says_otherwise(school, setup):
    csv = "first_name,last_name,class\n" + "".join(f"Pupil{n},Test,JSS1 A\n" for n in range(3))
    with schema_context(school.schema_name):
        refused = import_students(csv, session=setup["session"], tenant=school)
        assert [e.code for e in refused.errors] == ["ARM_FULL"]

        allowed = import_students(
            csv, session=setup["session"], tenant=school, enforce_capacity=False
        )
        assert allowed.ok, allowed.errors
        assert Enrolment.objects.count() == 3
