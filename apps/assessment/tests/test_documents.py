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


def test_transcript_renders_with_gpa_for_a_polytechnic():
    pdf = render_pdf(
        "assessment/transcript.html",
        {
            "branding": branding(POLY),
            "transcript": {
                "student": student(display_number="CSC/25/0001"),
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
