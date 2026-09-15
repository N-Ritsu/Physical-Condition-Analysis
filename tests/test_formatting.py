import pytest

from health_dashboard.formatting import format_axis_value, format_clock


@pytest.mark.parametrize(
    ("hours", "expected"),
    [
        (21.5, "21:30"),
        (24.5, "00:30"),
        (29.75, "05:45"),
    ],
)
def test_format_clock(hours, expected):
    assert format_clock(hours) == expected


def test_format_axis_value_time():
    assert format_axis_value("bedtime_hours", 21.5) == "21:30"


def test_format_axis_value_quality_score():
    assert format_axis_value("quality_score", 3) == "良好"
    assert format_axis_value("quality_score", 2) == "普通"
    assert format_axis_value("quality_score", 1) == "悪い"


def test_format_axis_value_night_awakenings():
    assert format_axis_value("night_awakenings", 1.5) == "1.5回"


def test_format_axis_value_missing():
    assert format_axis_value("mood_wake", None) == "―"
