"""The grading arithmetic, without a database.

These are the rules a school will dispute — a boundary mark, a tie, a CGPA —
so they are tested in isolation and run anywhere.
"""

from decimal import Decimal

import pytest

from apps.assessment.grading import (
    Band,
    average,
    band_for,
    grade_point_average,
    ordinal,
    percentage,
    quantise,
    rank,
)
from apps.assessment.seeds import FIVE_POINT_BANDS, WAEC_BANDS


def bands(rows) -> list[Band]:
    return [
        Band(letter, Decimal(low), Decimal(high), Decimal(point), remark)
        for letter, low, high, point, remark in rows
    ]


@pytest.mark.parametrize(
    ("total", "obtainable", "expected"),
    [
        (75, 100, "75.00"),
        (0, 100, "0.00"),
        # 2/3 of 100 rounds half-up at the second place, not down.
        (Decimal("55.555"), 100, "55.56"),
        # Nothing obtainable is 0, not a crash: an unconfigured subject must
        # not take a whole broadsheet down with it.
        (10, 0, "0.00"),
    ],
)
def test_percentage(total, obtainable, expected):
    assert percentage(Decimal(total), Decimal(obtainable)) == Decimal(expected)


@pytest.mark.parametrize(
    ("mark", "grade"),
    [(100, "A1"), (75, "A1"), (74.99, "B2"), (70, "B2"), (50, "C6"), (40, "E8"), (0, "F9")],
)
def test_waec_boundaries(mark, grade):
    """75 is an A1 and 74.99 is not — the boundary is inclusive at the bottom."""
    assert band_for(bands(WAEC_BANDS), quantise(mark)).letter == grade


@pytest.mark.parametrize(
    ("mark", "grade", "point"), [(70, "A", 5), (69, "B", 4), (50, "C", 3), (39, "F", 0)]
)
def test_five_point_scale(mark, grade, point):
    band = band_for(bands(FIVE_POINT_BANDS), Decimal(mark))
    assert (band.letter, band.point) == (grade, Decimal(point))


def test_a_mark_below_every_band_has_no_grade():
    narrow = [Band("A", Decimal(80), Decimal(100))]
    assert band_for(narrow, Decimal(50)) is None


def test_a_fraction_between_two_whole_number_bands_still_grades():
    """44 out of 60 is 73.33, which falls in the gap between B2 (70–74) and
    B3 (65–69) if a scale is read as closed intervals."""
    assert band_for(bands(WAEC_BANDS), Decimal("73.33")).letter == "B2"
    assert band_for(bands(WAEC_BANDS), Decimal("39.99")).letter == "F9"


def test_overlapping_bands_resolve_to_the_better_grade():
    """Sloppy configuration should not silently downgrade a pupil."""
    overlapping = [
        Band("A", Decimal(70), Decimal(100)),
        Band("B", Decimal(60), Decimal(75)),
    ]
    assert band_for(overlapping, Decimal(72)).letter == "A"


def test_gpa_is_weighted_by_credit_units():
    # 5.0 over 6 units and 3.0 over 2 units: 36/8, not the plain mean of 4.0.
    assert grade_point_average([(Decimal(5), 6), (Decimal(3), 2)]) == Decimal("4.50")


def test_gpa_is_none_without_credit_units():
    """A secondary school has no GPA, and printing 0.00 would be a lie."""
    assert grade_point_average([(Decimal(5), 0), (Decimal(4), 0)]) is None


def test_gpa_ignores_zero_unit_subjects():
    assert grade_point_average([(Decimal(5), 3), (Decimal(0), 0)]) == Decimal("5.00")


def test_rank_is_competition_style():
    """Two pupils on 78 are both second and nobody is third."""
    assert rank([Decimal(90), Decimal(78), Decimal(78), Decimal(60)]) == [1, 2, 2, 4]


def test_rank_of_an_empty_cohort():
    assert rank([]) == []


def test_average_of_nothing_is_zero():
    assert average([]) == Decimal("0.00")


def test_average_rounds_half_up():
    assert average([Decimal("70.00"), Decimal("75.01")]) == Decimal("72.51")


@pytest.mark.parametrize(
    ("position", "text"),
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (21, "21st"), (0, "")],
)
def test_ordinal(position, text):
    assert ordinal(position) == text
