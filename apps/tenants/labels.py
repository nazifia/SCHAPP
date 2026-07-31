"""Terminology resolver.

A secondary school has Terms, Classes and Subjects; a polytechnic has
Semesters, Levels and Courses. Same tables, different words. Every label the
UI shows comes from here (or a tenant override), never from a hardcoded
string in a widget or a PDF template.
"""

from .models import InstitutionType

_SECONDARY = {
    "INSTITUTION": "School",
    "SESSION": "Session",
    "TERM": "Term",
    "CLASS": "Class",
    "CLASS_GROUP": "Arm",
    "SUBJECT": "Subject",
    "STUDENT": "Student",
    "TEACHER": "Teacher",
    "HEAD": "Principal",
    "REPORT": "Report card",
    "RESULT_SHEET": "Broadsheet",
    "GUARDIAN": "Parent/Guardian",
    "ENROLMENT": "Enrolment",
}

_TERTIARY = {
    "INSTITUTION": "Institution",
    "SESSION": "Session",
    "TERM": "Semester",
    "CLASS": "Level",
    "CLASS_GROUP": "Programme",
    "SUBJECT": "Course",
    "STUDENT": "Student",
    "TEACHER": "Lecturer",
    "HEAD": "Rector/Provost",
    "REPORT": "Result slip",
    "RESULT_SHEET": "Result sheet",
    "GUARDIAN": "Next of kin",
    "ENROLMENT": "Course registration",
}


def labels_for(institution_type: str, overrides: dict | None = None) -> dict[str, str]:
    base = _TERTIARY if institution_type == InstitutionType.TERTIARY else _SECONDARY
    return {**base, **(overrides or {})}
