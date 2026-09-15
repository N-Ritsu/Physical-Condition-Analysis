import datetime as dt

import pandas as pd

from health_dashboard.rule_based_insight import generate_rule_based_insight


def _df(values):
    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(len(values))]
    return pd.DataFrame({"date": dates, "mood_wake": values})


def test_generate_rule_based_insight_no_data():
    df = _df([])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "データがありません" in text


def test_generate_rule_based_insight_stable_trend():
    df = _df([5, 5, 5, 5, 5, 5])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "ほぼ横ばい" in text
    assert "面談の際に" in text


def test_generate_rule_based_insight_rising_trend():
    df = _df([1, 1, 1, 8, 8, 8])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "増える傾向" in text


def test_generate_rule_based_insight_falling_trend():
    df = _df([8, 8, 8, 1, 1, 1])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "減る傾向" in text


def test_generate_rule_based_insight_never_mentions_medical_terms():
    df = _df([1, 2, 3, 9, 9, 9])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    for banned in ("診断", "病気", "薬", "服薬"):
        assert banned not in text
