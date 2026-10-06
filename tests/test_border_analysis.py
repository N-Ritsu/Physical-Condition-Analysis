import datetime as dt

import pandas as pd
import pytest

from health_dashboard.border_analysis import (
    count_recorded_days,
    describe_border,
    find_bad_borders,
    heaviest_border_exceeded,
)
from health_dashboard.rule_based_insight import generate_rule_based_insight


def _df(rows, axis_col="night_awakenings"):
    """rows: (値, 体調ラベル) のリスト。日付は連続させる。"""
    start = dt.date(2026, 1, 1)
    return pd.DataFrame(
        {
            "date": [start + dt.timedelta(days=i) for i in range(len(rows))],
            axis_col: [r[0] for r in rows],
            "condition_label": [r[1] for r in rows],
        }
    )


def _rows(spec):
    """spec: {値: (総日数, ×の日数)} を (値, ラベル) のリストに展開する。"""
    rows = []
    for value, (days, bad_days) in spec.items():
        rows += [(value, "注意")] * bad_days + [(value, "良好")] * (days - bad_days)
    return rows


def test_lower_direction_finds_high_side_border_at_each_level():
    # 0回:10日(×0)、1回:6日(×0)、2回:5日(×3)、3回:5日(×4)、4回:5日(×5)
    df = _df(_rows({0: (10, 0), 1: (6, 0), 2: (5, 3), 3: (5, 4), 4: (5, 5)}))

    borders = find_bad_borders(df, "night_awakenings", "lower")

    by_report = {b.report: b for b in borders}
    # 3回以上: 10日中9日=90% → 極めて危険な報告（90%以上を満たす最も手前のしきい値）
    assert by_report["極めて危険な報告"].side == "high"
    assert by_report["極めて危険な報告"].threshold == 3
    assert by_report["極めて危険な報告"].rate == pytest.approx(0.9)
    # 2回以上: 15日中12日=80% → 危険な報告
    assert by_report["危険な報告"].threshold == 2
    assert by_report["危険な報告"].rate == pytest.approx(12 / 15)
    # 70%以上を満たす最も手前も2回以上で、危険な報告と同じボーダーのため重複して出さない
    assert "注意すべき報告" not in by_report


def test_same_border_meeting_several_levels_is_reported_once_at_heaviest_level():
    df = _df(_rows({0: (12, 0), 1: (5, 0), 3: (6, 6)}))

    borders = find_bad_borders(df, "night_awakenings", "lower")

    assert [(b.report, b.threshold) for b in borders] == [("極めて危険な報告", 3)]


def test_higher_direction_uses_low_side():
    df = _df(_rows({1: (4, 4), 2: (8, 1), 3: (10, 0)}), axis_col="quality_score")

    borders = find_bad_borders(df, "quality_score", "higher")

    assert len(borders) == 1
    assert borders[0].side == "low"
    assert borders[0].threshold == 1
    assert borders[0].is_beyond(1) is True
    assert borders[0].is_beyond(2) is False


def test_none_direction_checks_both_sides():
    # 起床時間が早い日（5.5以下）に×が集中している
    df = _df(_rows({5.5: (5, 5), 7.0: (10, 1), 9.0: (8, 0)}), axis_col="wake_hours")

    borders = find_bad_borders(df, "wake_hours", "none")

    assert len(borders) == 1
    assert borders[0].side == "low"
    assert borders[0].threshold == 5.5


def test_too_few_days_beyond_border_is_not_adopted():
    # 3日だけ×（MIN_DAYS_BEYOND=4未満）。偶然の可能性があるため採用しない。
    df = _df(_rows({0: (20, 0), 5: (3, 3)}))

    assert find_bad_borders(df, "night_awakenings", "lower") == []


def test_not_adopted_when_bad_condition_is_common_everywhere():
    # どの値でも7割以上が×の人は、特定の値を「ボーダー」とは言えない。
    df = _df(_rows({0: (6, 5), 1: (6, 5), 2: (6, 5), 3: (6, 5)}))

    assert find_bad_borders(df, "night_awakenings", "lower") == []


def test_too_few_recorded_days_returns_empty():
    df = _df(_rows({0: (3, 0), 3: (4, 4)}))  # 7日分のみ

    assert find_bad_borders(df, "night_awakenings", "lower") == []


def test_days_without_condition_record_are_ignored():
    rows = _rows({0: (12, 0), 3: (5, 5)}) + [(3, None)] * 10  # 記録なしの日は数えない
    df = _df(rows)

    borders = find_bad_borders(df, "night_awakenings", "lower")

    assert borders[0].days_beyond == 5


def test_without_condition_column_returns_empty():
    df = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "night_awakenings": [3]})

    assert find_bad_borders(df, "night_awakenings", "lower") == []


# --- 表示用の文章・直近の判定 ---


def _borders_for_three_or_more():
    df = _df(_rows({0: (12, 0), 1: (5, 0), 3: (6, 6)}))
    return find_bad_borders(df, "night_awakenings", "lower")


def test_describe_border_includes_report_condition_and_counts():
    border = _borders_for_three_or_more()[0]

    text = describe_border("night_awakenings", "中途覚醒回数", border)

    assert text == (
        "【極めて危険な報告】中途覚醒回数が3回以上の日は、"
        "体調が×（注意以上）になる割合が100%（6日中6日）でした。"
    )


@pytest.mark.parametrize(
    ("axis_col", "label", "side", "threshold", "expected"),
    [
        ("quality_score", "睡眠の質", "low", 1.0, "睡眠の質が「悪い」の日"),
        ("quality_score", "睡眠の質", "low", 2.0, "睡眠の質が「普通」以下の日"),
        ("mood_wake", "気分（起床時）", "low", 4.0, "気分（起床時）が4以下の日"),
        ("wake_hours", "起床時間", "low", 5.5, "起床時間が05:30以前の日"),
        ("bedtime_hours", "入眠時間", "high", 24.5, "入眠時間が00:30以降の日"),
        ("sleep_duration_hours", "睡眠時間", "low", 6.5, "睡眠時間が6.5時間以下の日"),
        ("pressure_hpa", "気圧（日平均）", "low", 1002.04, "気圧（日平均）が1002.0hPa以下の日"),
        ("pressure_change_hpa", "気圧（前日差）", "low", -3.0, "気圧（前日差）が-3.0hPa以下の日"),
        ("pressure_change_hpa", "気圧（前日差）", "high", 3.0, "気圧（前日差）が+3.0hPa以上の日"),
    ],
)
def test_describe_condition_wording(axis_col, label, side, threshold, expected):
    from health_dashboard.border_analysis import BadBorder, describe_condition

    border = BadBorder("危険な報告", 0.8, side, threshold, 5, 4)

    assert describe_condition(axis_col, label, border) == expected


def test_heaviest_border_exceeded_returns_most_severe_match():
    df = _df(_rows({0: (10, 0), 1: (6, 0), 2: (5, 3), 3: (5, 4), 4: (5, 5)}))
    borders = find_bad_borders(df, "night_awakenings", "lower")

    assert heaviest_border_exceeded(borders, 3).report == "極めて危険な報告"
    assert heaviest_border_exceeded(borders, 2).report == "危険な報告"
    assert heaviest_border_exceeded(borders, 1) is None
    assert heaviest_border_exceeded(borders, None) is None
    assert heaviest_border_exceeded([], 5) is None


# --- 振り返りコメントとの関係（ボーダーの文章は含めず、締めの判定にだけ使う） ---


def _insight(values, borders):
    df = pd.DataFrame(
        {
            "date": [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(len(values))],
            "night_awakenings": values,
        }
    )
    return generate_rule_based_insight(
        df, "night_awakenings", "中途覚醒回数", "全期間", borders=borders
    )


def test_insight_text_does_not_contain_border_sentences():
    text = _insight([0, 0, 0, 0, 3], _borders_for_three_or_more())

    assert "【" not in text
    assert "ボーダー" not in text


def test_closing_warns_when_latest_value_exceeds_border():
    text = _insight([0, 0, 0, 0, 0, 0, 0, 0, 0, 3], _borders_for_three_or_more())

    assert "気にかけてあげたほうが良いかもしれません" in text


def test_closing_unchanged_when_latest_value_is_below_border():
    with_borders = _insight([0] * 10, _borders_for_three_or_more())
    without_borders = _insight([0] * 10, None)

    assert with_borders == without_borders


def test_count_recorded_days_counts_only_days_with_value_and_condition():
    rows = _rows({0: (6, 1), 3: (4, 4)}) + [(3, None), (None, "注意")]
    df = _df(rows)

    assert count_recorded_days(df, "night_awakenings") == (10, 5)


def test_count_recorded_days_without_condition_column():
    df = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "night_awakenings": [3]})

    assert count_recorded_days(df, "night_awakenings") == (0, 0)


def test_pressure_change_border_combines_drops_and_rises_around_zero():
    # 下がった日が3日、上がった日が3日。片側ずつでは4日未満だが、合わせると6日で全部×。
    spec = {-5.0: (3, 3), 4.0: (3, 3), 0.0: (12, 1), 1.0: (8, 0), -1.0: (6, 0)}
    df = _df(_rows(spec), axis_col="pressure_change_hpa")

    borders = find_bad_borders(df, "pressure_change_hpa", "none")

    assert len(borders) >= 1
    border = borders[0]
    assert border.side == "far"
    assert border.threshold == 4.0
    assert border.days_beyond == 6 and border.bad_days == 6
    assert border.center == 0.0
    assert border.line_values == [-4.0, 4.0]
    assert border.is_beyond(-5.0) is True
    assert border.is_beyond(4.0) is True
    assert border.is_beyond(-3.9) is False
    assert border.is_beyond(0.0) is False


def test_pressure_change_far_border_wording():
    from health_dashboard.border_analysis import BadBorder

    border = BadBorder("極めて危険な報告", 0.9, "far", 3.0, 6, 6)

    assert describe_border("pressure_change_hpa", "気圧（前日差）", border) == (
        "【極めて危険な報告】気圧（前日差）が前日から上下に3.0hPa以上動いた日は、"
        "体調が×（注意以上）になる割合が100%（6日中6日）でした。"
    )


def test_other_axes_are_not_treated_as_centered():
    # 気圧（日平均）には「ちょうど良い値」が無いため、従来どおり高い側・低い側を別々に調べる。
    spec = {1000.0: (5, 5), 1010.0: (20, 1), 1020.0: (5, 0)}
    df = _df(_rows(spec), axis_col="pressure_hpa")

    borders = find_bad_borders(df, "pressure_hpa", "none")

    assert borders and borders[0].side == "low"
