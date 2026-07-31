"""Structure seeding — the shape a new school starts with."""

from apps.academics.seeds import SECONDARY_LEVELS, STREAMS, TERTIARY_LEVELS


def test_secondary_gets_the_six_nigerian_levels():
    codes = [code for code, _, _ in SECONDARY_LEVELS]
    assert codes == ["JSS1", "JSS2", "JSS3", "SSS1", "SSS2", "SSS3"]


def test_tertiary_gets_100_to_500():
    codes = [code for code, _, _ in TERTIARY_LEVELS]
    assert codes == ["100", "200", "300", "400", "500"]


def test_levels_are_ordered_without_gaps():
    for levels in (SECONDARY_LEVELS, TERTIARY_LEVELS):
        orders = [order for _, _, order in levels]
        assert orders == list(range(1, len(levels) + 1))


def test_the_three_senior_secondary_streams_exist():
    assert {code for code, _ in STREAMS} == {"science", "arts", "commercial"}
