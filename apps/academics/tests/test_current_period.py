"""Adding and setting the current session/term.

The invariant under test: the current term always sits inside the current
session. Both flags were independently writable booleans, so a rollover that
touched one and not the other ran the school in two academic years at once —
scores and registration keyed to `current_term()`, enrolment, class lists and
invoicing keyed to `session__is_current`.
"""

import json

import pytest

from apps.academics.models import AcademicSession, Term
from apps.academics.selectors import current_session, current_term
from apps.academics.services import set_current_session, set_current_term
from apps.accounts.models import Role, User
from apps.audit.models import AuditAction, AuditLog
from apps.tenants.db import schema_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.db_required]

SESSIONS = "/api/v1/academics/sessions/"
TERMS = "/api/v1/academics/terms/"


@pytest.fixture
def school(make_tenant, ncc_table):
    return make_tenant("kings-college")


@pytest.fixture
def registrar(school):
    with schema_context(school.schema_name):
        user = User.objects.create_user("+2348035555555", first_name="Ada")
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


def _terms(session, *, current_index=None):
    """Three terms, each a real slice of the session's year."""
    spans = [
        ("First Term", "2025-09-01", "2025-12-12"),
        ("Second Term", "2026-01-06", "2026-04-03"),
        ("Third Term", "2026-04-20", "2026-07-31"),
    ]
    return [
        Term.objects.create(
            session=session,
            index=index,
            name=name,
            start_date=start,
            end_date=end,
            is_current=index == current_index,
        )
        for index, (name, start, end) in enumerate(spans, start=1)
    ]


@pytest.fixture
def years(school):
    with schema_context(school.schema_name):
        old = AcademicSession.objects.create(
            name="2025/2026", start_date="2025-09-01", end_date="2026-07-31", is_current=True
        )
        new = AcademicSession.objects.create(
            name="2026/2027", start_date="2026-09-01", end_date="2027-07-31"
        )
        yield {"old": old, "old_terms": _terms(old, current_index=1), "new": new}


def _post(client, url, headers, body=None):
    return client.post(url, data=json.dumps(body or {}), content_type="application/json", **headers)


# --- the invariant -----------------------------------------------------------


def test_rolling_the_session_over_takes_the_term_with_it(school, years):
    """Was the split-brain bug: a new session, last year's term still current."""
    with schema_context(school.schema_name):
        _terms(years["new"])
        set_current_session(session=years["new"])

        assert current_session() == years["new"]
        assert current_term().session_id == years["new"].pk
        assert not Term.objects.filter(is_current=True, session=years["old"]).exists()


def test_a_session_with_no_terms_leaves_no_current_term(school, years):
    """Better nothing than a term in the wrong year."""
    with schema_context(school.schema_name):
        set_current_session(session=years["new"])
        assert current_term() is None


def test_setting_a_term_promotes_its_session(school, years):
    with schema_context(school.schema_name):
        second = _terms(years["new"])[1]
        set_current_term(term=second)

        assert current_term() == second
        assert current_session() == years["new"]
        years["old"].refresh_from_db()
        assert not years["old"].is_current


def test_a_bare_model_save_cannot_break_the_pair(school, years):
    """The invariant lives in `save()`, so seeds and the admin obey it too."""
    with schema_context(school.schema_name):
        years["new"].is_current = True
        years["new"].save()
        assert Term.objects.filter(is_current=True).count() == 0

        stray = _terms(years["new"])[0]
        stray.is_current = True
        stray.save()
        years["new"].refresh_from_db()
        assert years["new"].is_current


def test_advancing_within_a_year_leaves_the_session_alone(school, years):
    with schema_context(school.schema_name):
        set_current_term(term=years["old_terms"][1])
        assert current_session() == years["old"]
        assert current_term().index == 2


# --- over HTTP ---------------------------------------------------------------


def test_set_current_endpoints_reach_the_services(client, school, years, headers):
    with schema_context(school.schema_name):
        _terms(years["new"])

    response = _post(client, f"{SESSIONS}{years['new'].pk}/set-current/", headers)
    assert response.status_code == 200
    assert response.json()["is_current"] is True

    with schema_context(school.schema_name):
        assert current_term().session_id == years["new"].pk
        assert AuditLog.objects.filter(action=AuditAction.SESSION_SET_CURRENT).count() == 1

    with schema_context(school.schema_name):
        third = Term.objects.get(session=years["new"], index=3)
    response = _post(client, f"{TERMS}{third.pk}/set-current/", headers)
    assert response.status_code == 200
    assert response.json()["id"] == str(third.pk)

    with schema_context(school.schema_name):
        assert current_term() == third
        assert AuditLog.objects.filter(action=AuditAction.TERM_SET_CURRENT).count() == 1


def test_a_teacher_cannot_change_the_year(client, school, years, headers):
    from apps.auth_phone import tokens

    with schema_context(school.schema_name):
        teacher = User.objects.create_user("+2348036666666", first_name="Bola")
        teacher.roles.add(Role.objects.get(code="teacher"))
        pair = tokens.issue_for_user(teacher, tenant=school)

    response = _post(
        client,
        f"{SESSIONS}{years['new'].pk}/set-current/",
        {
            "HTTP_X_TENANT_SLUG": school.slug,
            "HTTP_AUTHORIZATION": f"Bearer {pair['access']}",
        },
    )
    assert response.status_code == 403
    with schema_context(school.schema_name):
        assert current_session() == years["old"]


# --- adding ------------------------------------------------------------------


def test_adding_a_session_and_a_term_over_the_api(client, school, headers):
    created = _post(
        client,
        SESSIONS,
        headers,
        {"name": "2027/2028", "start_date": "2027-09-01", "end_date": "2028-07-31"},
    )
    assert created.status_code == 201

    term = _post(
        client,
        TERMS,
        headers,
        {
            "session": created.json()["id"],
            "index": 1,
            "name": "First Term",
            "start_date": "2027-09-01",
            "end_date": "2027-12-10",
        },
    )
    assert term.status_code == 201
    assert term.json()["is_current"] is False


@pytest.mark.parametrize("url_key", ["session", "term"])
def test_a_period_that_ends_before_it_begins_is_refused(client, school, years, headers, url_key):
    """`Model.clean()` never runs under DRF — this had no API half."""
    if url_key == "session":
        url, body = (
            SESSIONS,
            {
                "name": "2028/2029",
                "start_date": "2028-09-01",
                "end_date": "2028-08-01",
            },
        )
    else:
        url, body = (
            TERMS,
            {
                "session": str(years["new"].pk),
                "index": 1,
                "name": "First Term",
                "start_date": "2026-12-01",
                "end_date": "2026-09-01",
            },
        )

    response = _post(client, url, headers, body)
    assert response.status_code == 400
    assert "end_date" in json.dumps(response.json())


def test_patching_one_bound_is_checked_against_the_stored_other(client, school, years, headers):
    response = client.patch(
        f"{SESSIONS}{years['old'].pk}/",
        data=json.dumps({"end_date": "2025-08-01"}),
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 400
