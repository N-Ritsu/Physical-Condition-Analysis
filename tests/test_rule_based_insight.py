import datetime as dt

import pandas as pd

from health_dashboard.rule_based_insight import generate_rule_based_insight


def _df(values, axis_col="mood_wake", condition_labels=None, start=dt.date(2026, 1, 1)):
    dates = [start + dt.timedelta(days=i) for i in range(len(values))]
    data = {"date": dates, axis_col: values}
    if condition_labels is not None:
        data["condition_label"] = condition_labels
    return pd.DataFrame(data)


def test_generate_rule_based_insight_no_data():
    df = _df([])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "データがありません" in text


def test_generate_rule_based_insight_stable_trend():
    df = _df([5, 5, 5, 5, 5, 5])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "ほぼ横ばい" in text
    assert "おおむね安定しており、大きな問題は見られなさそうです。" in text


def test_generate_rule_based_insight_never_mentions_medical_terms():
    df = _df([1, 2, 3, 9, 9, 9])
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    for banned in ("診断", "病気", "薬", "服薬"):
        assert banned not in text


# --- トレンドの方向性評価（AXIS_DIRECTION） ---


def test_trend_higher_is_better_rising_is_good():
    # mood_wake は「多いほど良い」軸
    df = _df([1, 1, 1, 8, 8, 8], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "増える傾向" in text
    assert "良い方向" in text


def test_trend_higher_is_better_falling_is_concerning():
    df = _df([8, 8, 8, 1, 1, 1], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "減る傾向" in text
    assert "注意したい方向" in text


def test_trend_lower_is_better_rising_is_concerning():
    # night_awakenings は「少ないほど良い」軸
    df = _df([0, 0, 0, 5, 5, 5], axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "全期間")
    assert "増える傾向" in text
    assert "注意したい方向" in text


def test_trend_lower_is_better_falling_is_good():
    df = _df([5, 5, 5, 0, 0, 0], axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "全期間")
    assert "減る傾向" in text
    assert "良い方向" in text


def test_trend_no_direction_axis_has_no_evaluation_wording():
    # bedtime_hours は方向性なし（安定度のみ評価）の軸
    df = _df([21.0, 21.0, 21.0, 23.0, 23.0, 23.0], axis_col="bedtime_hours")
    text = generate_rule_based_insight(df, "bedtime_hours", "入眠時間", "全期間")
    assert "増える傾向" in text
    assert "良い方向" not in text
    assert "注意したい方向" not in text


def test_trend_noisy_data_adds_hedge_phrase():
    # 全体としては増加傾向だが、日によるばらつきが非常に大きい（R^2が低い）ケース
    df = _df([5, 25, 5, 25, 5, 26], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "ばらつきも大きく" in text


# --- 外れ値検知（zスコア） ---


def test_outlier_detected_for_extreme_latest_value():
    df = _df([3, 5, 4, 6, 5, 4, 5, 6, 4, 20], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "かなり高い値になっています" in text


def test_outlier_not_detected_for_typical_latest_value():
    df = _df([3, 5, 4, 6, 5, 4, 5, 6, 4, 5], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "かなり高い値になっています" not in text
    assert "かなり低い値になっています" not in text


# --- 体調（セルフケアシート由来）のストリーク検知 ---


def test_condition_streak_detected():
    df = _df(
        [1, 2, 3, 4, 5, 6],
        axis_col="mood_wake",
        condition_labels=["良好", "良好", "普通", "注意", "警戒", "異常"],
    )
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "直近の記録3件連続で、体調が「注意」以上の状態になっています。" in text


def test_condition_streak_broken_by_earlier_good_day():
    df = _df(
        [1, 2, 3, 4, 5, 6],
        axis_col="mood_wake",
        condition_labels=["異常", "異常", "異常", "普通", "異常", "異常"],
    )
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "連続で、体調が" not in text


def test_condition_streak_broken_by_missing_record():
    df = _df(
        [1, 2, 3, 4, 5],
        axis_col="mood_wake",
        condition_labels=["異常", "異常", None, "異常", "異常"],
    )
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "連続で、体調が" not in text


# --- 安定度比較（ローリングウィンドウ） ---


def _alternating(low, high, n):
    return [low if i % 2 == 0 else high for i in range(n)]


def _calm(value, n):
    """安定期を表す完全に一定の値の列（標準偏差0）。"""
    return [value] * n


def test_stability_window_year_view_finds_best_and_worst_month():
    # 最初の4週間(28日)は安定、次の4週間（直近）は不安定なパターン
    values = _calm(5, 28) + _alternating(1, 10, 28)
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "1月1日〜1月28日が最も安定し" in text
    assert "1月29日〜2月25日にばらつきが最も大きく" in text
    assert "ばらつきが大きかった時期に近い状態です。" in text
    # 直近が最も不安定だった時期に近いため、締めは注意喚起になる
    assert "気になる変化が見られるため、気にかけてあげたほうが良いかもしれません。" in text


def test_sentence_order_places_level_between_stability_main_and_recent():
    # 安定度比較の「概要」→水準比較→安定度比較の「直近との比較」の順で
    # 並ぶこと（水準比較を安定度比較の途中に挟む）を確認する。
    values = _calm(5, 28) + _alternating(1, 10, 28)
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")

    stability_main_pos = text.index("1月1日〜1月28日が最も安定し")
    level_pos = text.index("また、")
    stability_recent_pos = text.index("直近1か月は平均")

    assert stability_main_pos < level_pos < stability_recent_pos


def test_stability_window_year_view_reports_stable_when_no_big_difference():
    values = _calm(5, 56)
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "安定度に大きな違いは見られませんでした" in text
    assert "同様に落ち着いています" in text
    assert "おおむね安定しており、大きな問題は見られなさそうです。" in text


def test_stability_window_year_view_reports_uniform_volatility():
    # 窓ごとの差は小さいが、常にジグザグしていて落ち着いた時期がない
    # （睡眠の質のように取りうる値の幅が狭い軸で起こりうるパターン）
    values = _alternating(1, 3, 56)
    df = _df(values, axis_col="quality_score")
    text = generate_rule_based_insight(df, "quality_score", "睡眠の質", "全期間")
    assert "落ち着いた時期は見られず、常にある程度のばらつきが続いています。" in text
    assert "気になる変化が見られるため、気にかけてあげたほうが良いかもしれません。" in text
    # 誤って「安定」と判定されていないことを確認する
    assert "安定度に大きな違いは見られませんでした" not in text
    assert "おおむね安定しており" not in text


def test_stability_window_month_view_finds_best_and_worst_week():
    # 1週目安定・2週目不安定・3週目安定・4週目（直近）も安定
    stable_week = _calm(5, 7)
    volatile_week = _alternating(1, 10, 7)
    values = stable_week + volatile_week + stable_week + stable_week
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "直近1ヶ月")
    assert "1月1日〜1月7日が最も安定し" in text
    assert "1月8日〜1月14日にばらつきが最も大きく" in text
    assert "安定していた時期に近い落ち着き具合です。" in text
    # 直近は安定側に近いが、期間内に不安定な週もあったため自己分析を促す
    assert "それぞれの期間での生活リズムを比較すると、重要な要素が見つかるかもしれません。" in text


def test_stability_window_skipped_for_one_week_view():
    values = _calm(5, 28) + _alternating(1, 10, 28)
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "直近1週間")
    assert "が最も安定し" not in text
    assert "安定度に大きな違いは見られませんでした" not in text


# --- 締めの一文の出し分け ---


def test_closing_warns_when_trend_is_concerning():
    df = _df([8, 8, 8, 1, 1, 1], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "気になる変化が見られるため、気にかけてあげたほうが良いかもしれません。" in text


def test_closing_warns_when_outlier_is_in_bad_direction():
    # mood_wake は「多いほど良い」軸のため、大きく低い外れ値は悪化方向
    df = _df([3, 5, 4, 6, 5, 4, 5, 6, 4, -5], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "気になる変化が見られるため" in text


def test_closing_not_warned_when_outlier_is_in_good_direction():
    # 大きく高い外れ値は「多いほど良い」軸にとって悪化方向ではない
    df = _df([3, 5, 4, 6, 5, 4, 5, 6, 4, 20], axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "気になる変化が見られるため" not in text


def test_closing_warns_when_condition_streak_active():
    df = _df(
        [1, 2, 3, 4, 5, 6],
        axis_col="mood_wake",
        condition_labels=["良好", "良好", "普通", "注意", "警戒", "異常"],
    )
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "全期間")
    assert "気になる変化が見られるため" in text


def test_closing_prompts_self_analysis_when_gap_is_large_but_recent_is_stable():
    # 方向性のない軸（wake_hours）を使い、水準比較は関与させず安定度比較のみで検証する。
    stable_month = _calm(5, 28)
    volatile_month = _alternating(1, 10, 28)
    values = stable_month + volatile_month + stable_month
    df = _df(values, axis_col="wake_hours")
    text = generate_rule_based_insight(df, "wake_hours", "起床時間", "全期間")
    assert "気になる変化が見られるため" not in text
    assert "それぞれの期間での生活リズムを比較すると、重要な要素が見つかるかもしれません。" in text


# --- 水準（良し悪し）比較。安定度とは別の観点であることを検証する。 ---


def test_level_window_reported_separately_from_stability():
    # 両方とも一定値（＝安定度に差はない）だが、水準（回数）は大きく異なるパターン。
    # 「安定していた期間」が「回数が少なかった期間」と誤解されないよう、
    # 安定度比較とは別の文で水準比較が言及されることを確認する。
    values = _calm(0, 28) + _calm(3, 28)
    df = _df(values, axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "全期間")
    assert "安定度に大きな違いは見られませんでした" in text
    assert "1月1日〜1月28日は中途覚醒回数が最も良い状態" in text
    assert "1月29日〜2月25日は注意したい状態" in text


def test_level_window_recent_bad_triggers_warning():
    values = _calm(0, 28) + _calm(3, 28)  # 直近＝水準が悪かった期間と一致
    df = _df(values, axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "全期間")
    assert "気になる変化が見られるため、気にかけてあげたほうが良いかもしれません。" in text


def test_level_window_recent_good_prompts_self_analysis_instead_of_warning():
    good, bad = _calm(0, 28), _calm(3, 28)
    values = good + bad + good  # 直近＝水準が良かった期間と一致
    df = _df(values, axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "全期間")
    assert "気になる変化が見られるため" not in text
    assert "それぞれの期間での生活リズムを比較すると、重要な要素が見つかるかもしれません。" in text


def test_level_window_not_evaluated_for_direction_none_axis():
    # 入眠時間・起床時間は方向性がないため、水準比較の文は出力されない
    values = _calm(21, 28) + _calm(23, 28)
    df = _df(values, axis_col="wake_hours")
    text = generate_rule_based_insight(df, "wake_hours", "起床時間", "全期間")
    assert "が最も良い状態" not in text


# --- 直近1週間: 期間内比較の代わりに、直近数日を絶対基準で評価する ---


def test_recent_absolute_warning_for_night_awakenings():
    values = [0, 0, 0, 0, 3, 4]  # 直近2日とも3回以上（絶対基準で悪い）
    df = _df(values, axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "直近1週間")
    assert "直近2日の中途覚醒回数に、注意したい値が含まれています。" in text
    assert "気になる変化が見られるため、気にかけてあげたほうが良いかもしれません。" in text


def test_recent_absolute_ok_for_night_awakenings():
    values = [0, 0, 0, 0, 1, 0]
    df = _df(values, axis_col="night_awakenings")
    text = generate_rule_based_insight(df, "night_awakenings", "中途覚醒回数", "直近1週間")
    assert "注意したい値が含まれています" not in text


def test_recent_absolute_warning_for_mood():
    values = [8, 8, 8, 8, 4, 3]  # 直近2日が5未満
    df = _df(values, axis_col="mood_wake")
    text = generate_rule_based_insight(df, "mood_wake", "気分（起床時）", "直近1週間")
    assert "直近2日の気分（起床時）に、注意したい値が含まれています。" in text


def test_recent_absolute_warning_for_quality_score():
    values = [3, 3, 3, 3, 3, 1]  # 直近1件が「悪い」カテゴリ
    df = _df(values, axis_col="quality_score")
    text = generate_rule_based_insight(df, "quality_score", "睡眠の質", "直近1週間")
    assert "注意したい値が含まれています" in text


def test_recent_absolute_check_not_applied_to_direction_none_axis():
    # 入眠時間・起床時間は方向性がないため絶対評価の対象外
    values = [21.0, 22.0, 23.0, 20.0, 21.5, 22.5]
    df = _df(values, axis_col="bedtime_hours")
    text = generate_rule_based_insight(df, "bedtime_hours", "入眠時間", "直近1週間")
    assert "注意したい値が含まれています" not in text
