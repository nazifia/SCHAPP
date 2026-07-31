"""The grading arithmetic, with no database in it.

Everything here is a pure function over `Decimal`s so the rules a school will
argue about — where a boundary sits, how a tie is ranked, what a CGPA is —
can be tested without a Postgres schema, and reused by the report card, the
broadsheet and the transcript alike.

Money-and-marks arithmetic is `Decimal`, never `float`: a 74.995 that prints
as 75.00 but grades as a B is the kind of bug a parent brings to the office.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0")


def quantise(value: Decimal | int | float | str) -> Decimal:
    """Two decimal places, half-up — what a Nigerian result sheet prints."""
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Band:
    """One row of a grading scale: A1, 75–100, 5.00 points, "Excellent"."""

    letter: str
    minimum: Decimal
    maximum: Decimal
    point: Decimal = ZERO
    remark: str = ""

    def contains(self, percentage: Decimal) -> bool:
        """Strict membership. Used to validate a scale, not to grade with —
        see `band_for` for why grading only looks at the lower bound."""
        return self.minimum <= percentage <= self.maximum


def percentage(total: Decimal, max_total: Decimal) -> Decimal:
    """Score as a percentage of what was obtainable.

    A subject with no components configured has nothing obtainable, and 0/0 is
    0 here rather than an exception — an unconfigured subject must not break a
    whole broadsheet.
    """
    if not max_total:
        return ZERO
    return quantise(Decimal(total) * 100 / Decimal(max_total))


def band_for(bands: Iterable[Band], value: Decimal) -> Band | None:
    """The grade for a percentage: the best band whose lower bound it clears.

    Only the lower bound is consulted, and that is deliberate. Schools write
    their scales in whole numbers — 70–74 is a B2, 65–69 a B3 — while a
    percentage computed from a mark sheet is not whole: 44 out of 60 is 73.33,
    which falls in the *gap* between two bands and would otherwise come back
    ungraded. Sorting by lower bound descending also means overlapping or
    sloppily entered bands resolve to the better grade rather than to whichever
    row was created first.
    """
    for band in sorted(bands, key=lambda b: b.minimum, reverse=True):
        if value >= band.minimum:
            return band
    return None


def average(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return ZERO
    return quantise(sum(values, ZERO) / Decimal(len(values)))


def grade_point_average(entries: Iterable[tuple[Decimal, int]]) -> Decimal | None:
    """Credit-weighted GPA from (grade point, credit units) pairs.

    Returns None when nothing carries credit units — secondary schools do not
    have a GPA, and printing 0.00 on a JSS1 report card would be a lie. Their
    report card shows the average instead.
    """
    weighted = ZERO
    units = 0
    for point, unit in entries:
        if unit <= 0:
            continue
        weighted += Decimal(point) * unit
        units += unit
    if units == 0:
        return None
    return quantise(weighted / units)


def rank(values: Sequence[Decimal]) -> list[int]:
    """Competition ranking: 1, 2, 2, 4.

    Two pupils on 78 are both second and nobody is third. Dense ranking would
    make the sheet disagree with the class size.
    """
    ordered = sorted(range(len(values)), key=lambda i: values[i], reverse=True)
    positions = [0] * len(values)
    previous: Decimal | None = None
    previous_position = 0
    for offset, index in enumerate(ordered, start=1):
        if previous is not None and values[index] == previous:
            positions[index] = previous_position
        else:
            positions[index] = offset
            previous_position = offset
            previous = values[index]
    return positions


def ordinal(position: int) -> str:
    """1st, 2nd, 3rd, 11th — for the printed report card."""
    if position <= 0:
        return ""
    if 11 <= position % 100 <= 13:
        return f"{position}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(position % 10, "th")
    return f"{position}{suffix}"
