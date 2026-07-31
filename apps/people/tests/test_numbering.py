"""Number formats — pure string work, no database."""

import pytest

from apps.people.numbering import (
    DEFAULT_MATRIC_FORMAT,
    NumberContext,
    NumberFormatError,
    prefix_of,
    render,
)


def test_the_spec_example_format():
    ctx = NumberContext(year=2025, dept="CSC")
    assert render(DEFAULT_MATRIC_FORMAT, ctx, 42) == "CSC/25/0042"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("{yy}/{serial}", "25/0042"),
        ("{yyyy}/{serial}", "2025/0042"),
        ("KC/{yyyy}/{serial}", "KC/2025/0042"),
        ("{dept}-{yy}-{serial}", "CSC-25-0042"),
        ("{prog}/{yy}/{serial}", "ND-CSC/25/0042"),
        ("{dept}/{level}/{yy}/{serial}", "CSC/100/25/0042"),
    ],
)
def test_formats_render_as_written(fmt, expected):
    ctx = NumberContext(year=2025, dept="CSC", prog="ND-CSC", level="100")
    assert render(fmt, ctx, 42) == expected


def test_serial_is_zero_padded_and_grows():
    ctx = NumberContext(year=2025, dept="CSC")
    assert render(DEFAULT_MATRIC_FORMAT, ctx, 1).endswith("0001")
    assert render(DEFAULT_MATRIC_FORMAT, ctx, 9999).endswith("9999")
    # Past the pad width it keeps counting rather than wrapping or truncating.
    assert render(DEFAULT_MATRIC_FORMAT, ctx, 12345).endswith("12345")


def test_unknown_placeholder_is_rejected_loudly():
    with pytest.raises(NumberFormatError):
        render("{campus}/{serial}", NumberContext(year=2025), 1)


def test_a_placeholder_the_record_cannot_fill_is_rejected():
    """`/26/0001` is not a matriculation number.

    A secondary-school student has no programme, so `{dept}` was empty and the
    default matric format collapsed to a number that is missing its prefix and
    never reissued.
    """
    with pytest.raises(NumberFormatError):
        render(DEFAULT_MATRIC_FORMAT, NumberContext(year=2026), 1)


def test_prefix_groups_serials_by_department_and_year():
    assert prefix_of(DEFAULT_MATRIC_FORMAT, NumberContext(year=2025, dept="CSC")) == "CSC/25/"
    assert prefix_of(DEFAULT_MATRIC_FORMAT, NumberContext(year=2026, dept="CSC")) == "CSC/26/"
    assert prefix_of(DEFAULT_MATRIC_FORMAT, NumberContext(year=2025, dept="ACC")) == "ACC/25/"


def test_year_rolls_over_two_digits_correctly():
    assert render("{yy}/{serial}", NumberContext(year=2099), 1) == "99/0001"
    assert render("{yy}/{serial}", NumberContext(year=2100), 1) == "00/0001"
