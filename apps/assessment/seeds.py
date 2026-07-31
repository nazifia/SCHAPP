"""Grading scales and mark-sheet columns a new school starts with.

Seeded at provisioning so an exams officer can enter marks on day one instead
of designing a grading scale first. Everything is `get_or_create`, so
re-provisioning never overwrites a school that has since changed its own
bands — which schools do, and are entitled to.

The defaults are the conventional ones: WAEC's nine-point letter grades for a
secondary school, and the 5-point CGPA scale used by Nigerian polytechnics
and most universities for a tertiary institution.
"""

import logging
from decimal import Decimal

from .models import AssessmentComponent, GradeBand, GradingScale

logger = logging.getLogger(__name__)

#: code, name, mark allocation, order — 20 + 20 + 60 = 100.
SECONDARY_COMPONENTS = [
    ("CA1", "First C.A.", Decimal("20.00"), 1),
    ("CA2", "Second C.A.", Decimal("20.00"), 2),
    ("EXAM", "Examination", Decimal("60.00"), 3),
]

TERTIARY_COMPONENTS = [
    ("CA", "Continuous Assessment", Decimal("30.00"), 1),
    ("EXAM", "Examination", Decimal("70.00"), 2),
]

#: letter, minimum, maximum, grade point, remark
WAEC_BANDS = [
    ("A1", 75, 100, 0, "Excellent"),
    ("B2", 70, 74, 0, "Very good"),
    ("B3", 65, 69, 0, "Good"),
    ("C4", 60, 64, 0, "Credit"),
    ("C5", 55, 59, 0, "Credit"),
    ("C6", 50, 54, 0, "Credit"),
    ("D7", 45, 49, 0, "Pass"),
    ("E8", 40, 44, 0, "Pass"),
    ("F9", 0, 39, 0, "Fail"),
]

FIVE_POINT_BANDS = [
    ("A", 70, 100, 5, "Excellent"),
    ("B", 60, 69, 4, "Very good"),
    ("C", 50, 59, 3, "Good"),
    ("D", 45, 49, 2, "Fair"),
    ("E", 40, 44, 1, "Pass"),
    ("F", 0, 39, 0, "Fail"),
]


def seed_grading(institution_type: str) -> int:
    """Create the default scale and mark-sheet columns. Safe to run twice."""
    tertiary = institution_type == "TERTIARY"
    name = "5-point CGPA" if tertiary else "WAEC nine-point"
    bands = FIVE_POINT_BANDS if tertiary else WAEC_BANDS

    scale, created = GradingScale.objects.get_or_create(
        name=name,
        defaults={
            "is_default": True,
            "uses_grade_points": tertiary,
            "pass_percentage": Decimal("40.00"),
        },
    )
    if created:
        GradeBand.objects.bulk_create(
            GradeBand(
                scale=scale,
                letter=letter,
                minimum=Decimal(minimum),
                maximum=Decimal(maximum),
                grade_point=Decimal(point),
                remark=remark,
            )
            for letter, minimum, maximum, point, remark in bands
        )

    components = TERTIARY_COMPONENTS if tertiary else SECONDARY_COMPONENTS
    for code, label, max_score, order in components:
        AssessmentComponent.objects.get_or_create(
            code=code,
            level=None,
            subject=None,
            defaults={"name": label, "max_score": max_score, "order": order},
        )

    return len(components)
