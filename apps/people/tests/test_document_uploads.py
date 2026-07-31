"""Student documents are validated at the boundary they cross.

`StudentDocument.file` was a bare `FileField`: any extension, any size. These
files are served back from the application's own origin, so an `.html` or an
`.svg` among them is stored XSS against every session on that host — and the
`virus_scanned_at` column next to it, whose comment promised a Phase 9 scanner
that Phase 9 never built, made the whole thing look guarded.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Role, User
from apps.people.models import MAX_DOCUMENT_BYTES, StudentDocument
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

URL = "/api/v1/people/documents/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def student(school):
    from apps.people.models import Student

    with schema_context(school.schema_name):
        yield Student.objects.create(
            admission_number="KC/25/0001", first_name="Ada", last_name="Obi"
        )


@pytest.fixture
def headers(school):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348031111111", first_name="Registrar")
        user.roles.add(Role.objects.get(code="school_admin"))
        pair = tokens.issue_for_user(user, tenant=school)
    return {
        "HTTP_X_TENANT_SLUG": school.slug,
        "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
    }


def _upload(client, headers, student, name, content=b"%PDF-1.4 hello"):
    return client.post(
        URL,
        {
            "student": str(student.pk),
            "title": "Birth certificate",
            "file": SimpleUploadedFile(name, content),
        },
        **headers,
    )


def test_a_real_document_uploads(client, headers, student, school):
    assert _upload(client, headers, student, "birth-certificate.pdf").status_code == 201
    with schema_context(school.schema_name):
        assert StudentDocument.objects.count() == 1


@pytest.mark.parametrize(
    "name",
    [
        "payload.html",  # renders in the app's origin: stored XSS
        "payload.svg",  # an SVG is a script container
        "shell.php",
        "notes.txt",
    ],
)
def test_a_file_the_browser_would_execute_is_refused(client, headers, student, school, name):
    response = _upload(client, headers, student, name, content=b"<script>alert(1)</script>")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    with schema_context(school.schema_name):
        assert not StudentDocument.objects.exists()


def test_an_oversized_upload_is_refused(client, headers, student, school):
    oversized = b"x" * (MAX_DOCUMENT_BYTES + 1)
    response = _upload(client, headers, student, "huge.pdf", content=oversized)
    assert response.status_code == 400
    with schema_context(school.schema_name):
        assert not StudentDocument.objects.exists()


def test_the_extension_check_is_case_insensitive(client, headers, student):
    """A phone that names its camera roll `.JPG` is not an attack."""
    assert _upload(client, headers, student, "scan.JPG", content=b"\xff\xd8\xff").status_code == 201
