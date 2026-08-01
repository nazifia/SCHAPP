"""The printed documents render, without a database.

A report card that raises halfway through a template is discovered on results
day otherwise. These tests feed the templates plain objects — the same shape
the selectors build — and check that real PDF bytes come out.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.assessment.pdf import branding, render_pdf

pytest.importorskip("xhtml2pdf", reason="PDF rendering is an optional deployment dependency")

TENANT = SimpleNamespace(name="Kings College", institution_type="SECONDARY", slug="kings-college")
POLY = SimpleNamespace(name="Unity Polytechnic", institution_type="TERTIARY", slug="unity-poly")


def student(**overrides):
    defaults = {
        "full_name": "Ngozi Ali",
        "display_number": "KC/25/0001",
        "programme": None,
        "get_status_display": lambda: "Active",
    }
    return SimpleNamespace(**{**defaults, **overrides})


def term_result(**overrides):
    defaults = {
        "subjects_count": 2,
        "total_score": Decimal("155.00"),
        "max_total": Decimal("200.00"),
        "average": Decimal("77.50"),
        "credit_units_registered": 0,
        "credit_units_earned": 0,
        "gpa": None,
        "cgpa": None,
        "cohort_size": 30,
        "days_present": 58,
        "days_total": 60,
        "attendance_percentage": Decimal("96.67"),
        "form_teacher_comment": "A steady term.",
        "head_comment": "Keep it up.",
        "promotion_status": "PROMOTE",
        "get_promotion_status_display": lambda: "Promote",
    }
    return SimpleNamespace(**{**defaults, **overrides})


def subject_row(code="MTH", percentage="80.00", grade="A1"):
    return {
        "code": code,
        "title": "Mathematics",
        "credit_units": 0,
        "components": [
            {"code": "CA1", "score": Decimal("18.00"), "max_score": Decimal("20.00")},
            {"code": "EXAM", "score": Decimal("50.00"), "max_score": Decimal("60.00")},
        ],
        "total": Decimal("68.00"),
        "max_total": Decimal("80.00"),
        "percentage": Decimal(percentage),
        "grade": grade,
        "grade_point": Decimal("0.00"),
        "position": "2nd",
        "class_average": Decimal("64.20"),
        "class_highest": Decimal("91.00"),
        "remark": "Excellent",
        "is_carryover": False,
    }


def test_branding_uses_the_institution_type_labels():
    secondary = branding(TENANT)
    tertiary = branding(POLY)
    assert secondary["labels"]["TERM"] == "Term"
    assert secondary["labels"]["SUBJECT"] == "Subject"
    # The same template prints "Semester" and "Course" for a polytechnic.
    assert tertiary["labels"]["TERM"] == "Semester"
    assert tertiary["labels"]["SUBJECT"] == "Course"


def test_branding_survives_a_tenant_without_configuration():
    """The public-schema configuration row can be missing on a half-built
    tenant; a report card must still print."""
    assert branding(None)["institution"] == ""


def test_report_card_renders_to_pdf():
    pdf = render_pdf(
        "assessment/report_card.html",
        {
            "branding": branding(TENANT),
            "student": student(),
            "enrolment": SimpleNamespace(class_arm="JSS1 A", level="JSS 1"),
            "term": SimpleNamespace(name="First Term", index=1),
            "session": SimpleNamespace(name="2025/2026"),
            "result": term_result(),
            "position": "3rd",
            "subjects": [subject_row(), subject_row("ENG", "72.00", "B2")],
        },
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 1000


def test_report_card_renders_for_a_student_with_no_results():
    pdf = render_pdf(
        "assessment/report_card.html",
        {
            "branding": branding(TENANT),
            "student": student(),
            "enrolment": SimpleNamespace(class_arm="JSS1 A", level="JSS 1"),
            "term": SimpleNamespace(name="First Term", index=1),
            "session": SimpleNamespace(name="2025/2026"),
            "result": term_result(subjects_count=0, average=Decimal("0.00")),
            "position": "",
            "subjects": [],
        },
    )
    assert pdf.startswith(b"%PDF")


def test_broadsheet_renders_to_pdf():
    pdf = render_pdf(
        "assessment/broadsheet.html",
        {
            "branding": branding(TENANT),
            "sheet": {
                "term": "First Term 2025/2026",
                "subjects": [{"id": "1", "code": "MTH", "title": "Mathematics"}],
                "rows": [
                    {
                        "full_name": "Ngozi Ali",
                        "admission_number": "KC/25/0001",
                        # A student who never sat one subject leaves a hole in
                        # the grid; the template prints a dash, not a blank row.
                        "cells": [None],
                        "average": Decimal("77.50"),
                        "position": 1,
                    }
                ],
            },
        },
    )
    assert pdf.startswith(b"%PDF")


def test_a_tertiary_result_slip_leads_with_gpa_and_drops_the_position():
    """A polytechnic publishes a standing, not a place in a cohort."""
    from django.template.loader import render_to_string

    context = {
        "branding": branding(POLY),
        "student": student(display_number="CSC/25/0001"),
        "enrolment": SimpleNamespace(
            class_arm=None, level="100 Level", programme="ND Computer Science"
        ),
        "department": "Computing",
        "term": SimpleNamespace(name="First Semester", index=1),
        "session": SimpleNamespace(name="2025/2026"),
        "result": term_result(
            gpa=Decimal("4.20"),
            cgpa=Decimal("3.95"),
            credit_units_registered=12,
            credit_units_earned=12,
        ),
        "position": "3rd",
        "subjects": [subject_row()],
    }
    tertiary = render_to_string("assessment/report_card.html", context)
    assert "4.20" in tertiary and "3.95" in tertiary
    assert "Position" not in tertiary and "3rd" not in tertiary
    # Whose slip this is, and for which cohort.
    assert "Unity Polytechnic" in tertiary
    assert "100 Level" in tertiary and "ND Computer Science" in tertiary
    # The department owns the programme and files the slip.
    assert "Department" in tertiary and "Computing" in tertiary

    context["result"] = term_result()  # secondary: no grade points
    secondary = render_to_string("assessment/report_card.html", context)
    assert "Position" in secondary and "3rd" in secondary


def test_a_tertiary_broadsheet_closes_with_gpa_and_cgpa():
    from django.template.loader import render_to_string

    row = {
        "full_name": "Ngozi Ali",
        "admission_number": "CSC/25/0001",
        "cells": [None],
        "average": Decimal("71.00"),
        "position": 1,
        "gpa": Decimal("4.20"),
        "cgpa": Decimal("3.95"),
        "programme": "ND Computer Science",
        "department": "Computing",
    }
    sheet = {
        "term": "First Semester 2025/2026",
        "cohort": "100 Level",
        "department": "Computing",
        "programme": "ND Computer Science",
        "mixed_programmes": False,
        "subjects": [{"id": "1", "code": "CSC101", "title": "Intro to Computing"}],
        "rows": [row],
        "uses_grade_points": True,
    }
    html = render_to_string(
        "assessment/broadsheet.html", {"branding": branding(POLY), "sheet": sheet}
    )
    assert "GPA" in html and "4.20" in html and "3.95" in html
    assert "Pos" not in html and "71.00" not in html
    # A sheet nobody can file is a sheet nobody prints twice.
    assert "Unity Polytechnic" in html and "100 Level" in html
    assert "Computing" in html and "ND Computer Science" in html


def test_a_level_wide_sheet_names_the_programme_on_every_row():
    """One heading cannot cover a 100-level sheet that mixes two programmes."""
    from django.template.loader import render_to_string

    def row(name, programme):
        return {
            "full_name": name,
            "admission_number": "CSC/25/0001",
            "cells": [None],
            "gpa": Decimal("4.20"),
            "cgpa": Decimal("3.95"),
            "programme": programme,
            "department": "Computing",
        }

    html = render_to_string(
        "assessment/broadsheet.html",
        {
            "branding": branding(POLY),
            "sheet": {
                "term": "First Semester 2025/2026",
                "cohort": "100 Level",
                "department": "Computing",
                # Two programmes: no single heading, so a column instead.
                "programme": None,
                "mixed_programmes": True,
                "subjects": [{"id": "1", "code": "CSC101", "title": "Intro to Computing"}],
                "rows": [
                    row("Ngozi Ali", "ND Computer Science"),
                    row("Chidi Eze", "ND Statistics"),
                ],
                "uses_grade_points": True,
            },
        },
    )
    assert "ND Computer Science" in html and "ND Statistics" in html
    # The department is still shared, so it stays in the heading.
    assert "Computing" in html


def test_transcript_renders_with_gpa_for_a_polytechnic():
    pdf = render_pdf(
        "assessment/transcript.html",
        {
            "branding": branding(POLY),
            "transcript": {
                "student": student(display_number="CSC/25/0001"),
                "programme": "ND Computer Science",
                "department": "Computing",
                "terms": [
                    {
                        "session": "2025/2026",
                        "term": "First Semester",
                        "level": "100 Level",
                        "programme": "ND Computer Science",
                        "subjects": [
                            {
                                "code": "CSC101",
                                "title": "Intro to Computing",
                                "credit_units": 3,
                                "percentage": Decimal("71.00"),
                                "grade": "A",
                                "grade_point": Decimal("5.00"),
                            }
                        ],
                        "average": Decimal("71.00"),
                        "gpa": Decimal("5.00"),
                        "cgpa": Decimal("5.00"),
                        "credit_units_earned": 3,
                    }
                ],
                "cgpa": Decimal("5.00"),
                "total_credit_units": 3,
            },
        },
    )
    assert pdf.startswith(b"%PDF")
