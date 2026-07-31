"""Catalogue consistency — pure, no database."""

from apps.accounts.permissions_catalog import ALL, PERMISSIONS, SYSTEM_ROLES, is_known


def test_every_role_references_only_declared_permissions():
    for code, (_, permissions) in SYSTEM_ROLES.items():
        unknown = [p for p in permissions if not is_known(p)]
        assert not unknown, f"role {code} references undeclared permissions {unknown}"


def test_the_spec_roles_all_exist():
    expected = {
        "school_admin",
        "principal",
        "vice_principal",
        "registrar",
        "bursar",
        "teacher",
        "course_adviser",
        "librarian",
        "hostel_warden",
        "counsellor",
        "student",
        "guardian",
        "alumni",
    }
    assert expected <= set(SYSTEM_ROLES)


def test_only_the_owner_role_holds_the_wildcard():
    holders = [code for code, (_, perms) in SYSTEM_ROLES.items() if ALL in perms]
    assert holders == ["school_admin"]


def test_students_and_guardians_cannot_reach_other_peoples_data():
    for role in ("student", "guardian", "alumni"):
        _, permissions = SYSTEM_ROLES[role]
        assert "assessment.view_all_scores" not in permissions
        assert "people.view_student" not in permissions
        assert "assessment.publish_results" not in permissions


def test_only_exam_officers_and_heads_may_publish_results():
    publishers = {
        code for code, (_, perms) in SYSTEM_ROLES.items() if "assessment.publish_results" in perms
    }
    assert publishers == {"principal", "registrar"}


def test_teachers_cannot_publish_or_see_everything():
    _, permissions = SYSTEM_ROLES["teacher"]
    assert "assessment.enter_score" in permissions
    assert "assessment.publish_results" not in permissions
    assert "assessment.view_all_scores" not in permissions
    # A teacher sees the students they teach, not the whole school.
    assert "people.view_all_students" not in permissions


def test_permission_codes_are_namespaced():
    assert all("." in code for code in PERMISSIONS)
