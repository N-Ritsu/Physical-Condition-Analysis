import pandas as pd
import pytest

from health_dashboard.condition_scheme import (
    MIN_RECORDED_DAYS,
    apply_condition_scheme,
    build_condition_scheme,
)


def _points(distribution):
    """{点数: 日数} を点数のSeriesに展開する。"""
    values = []
    for point, days in distribution.items():
        values += [point] * days
    return pd.Series(values, dtype=float)


def _label_counts(scheme, points):
    return pd.Series([scheme.label_for(p) for p in points]).value_counts().to_dict()


def test_balances_four_groups_and_separates_outliers():
    # 0:15日, 1:9日, 2:5日, 3:3日, 4:5日, 外れ値 13, 20
    points = _points({0: 15, 1: 9, 2: 5, 3: 3, 4: 5, 13: 1, 20: 1})

    scheme = build_condition_scheme(points)

    assert _label_counts(scheme, points) == {
        "良好": 15,
        "普通": 9,
        "注意": 8,
        "警戒": 5,
        "異常": 2,
    }


def test_ranges_for_legend():
    points = _points({0: 15, 1: 9, 2: 5, 3: 3, 4: 5, 13: 1, 20: 1})

    scheme = build_condition_scheme(points)

    assert scheme.describe() == [
        ("良好", "0pt"),
        ("普通", "1pt"),
        ("注意", "2〜3pt"),
        ("警戒", "4pt"),
        ("異常", "7pt以上"),  # フェンス Q3+1.5×IQR = 6.25 を超える点数
    ]


def test_evenly_distributed_points_split_into_equal_groups():
    points = _points({0: 5, 1: 5, 2: 5, 3: 5})

    scheme = build_condition_scheme(points)

    assert _label_counts(scheme, points) == {"良好": 5, "普通": 5, "注意": 5, "警戒": 5}


def test_ties_prefer_fewer_days_in_the_most_severe_group():
    # 0:12, 1:12, 2:6, 3:4 だと 注意=2pt/警戒=3pt でも 注意=2〜3pt の2通りが考えられる。
    # 同じばらつきなら警戒が少ない方を選ぶ。
    points = _points({0: 10, 1: 10, 2: 5, 3: 5, 4: 5})

    scheme = build_condition_scheme(points)

    counts = _label_counts(scheme, points)
    assert counts["警戒"] <= counts["注意"]


def test_fewer_distinct_values_uses_fewer_labels_from_the_best():
    points = _points({0: 10, 1: 6})

    scheme = build_condition_scheme(points)

    assert _label_counts(scheme, points) == {"良好": 10, "普通": 6}
    assert scheme.outlier_above is None


def test_single_value_is_all_good():
    scheme = build_condition_scheme(_points({3: 20}))

    assert scheme.label_for(3) == "良好"


def test_small_values_are_never_outliers_even_if_far_from_the_rest():
    # ほとんどが0で、4点が少数ある人。外れ値でも5点未満は異常にしない。
    points = _points({0: 20, 4: 2})

    scheme = build_condition_scheme(points)

    assert scheme.outlier_above is None
    assert scheme.label_for(4) != "異常"


def test_outlier_is_above_iqr_fence_and_at_least_five_points():
    points = _points({0: 10, 1: 10, 2: 10, 3: 10, 7: 1})  # フェンスは Q3+1.5×IQR = 6.0

    scheme = build_condition_scheme(points)

    assert scheme.label_for(7) == "異常"
    assert scheme.label_for(6) != "異常"  # フェンスちょうどは外れ値にしない
    assert scheme.label_for(3) != "異常"


def test_too_few_records_returns_none():
    points = _points({0: MIN_RECORDED_DAYS - 1})

    assert build_condition_scheme(points) is None


def test_missing_points_are_ignored_and_labelled_none():
    points = pd.concat([_points({0: 8, 1: 8, 2: 8}), pd.Series([float("nan")] * 5)])

    scheme = build_condition_scheme(points)

    assert scheme.label_for(float("nan")) is None
    assert scheme.label_for(None) is None


def test_label_for_is_monotonic_in_points():
    points = _points({0: 15, 1: 9, 2: 5, 3: 3, 4: 5, 13: 1, 20: 1})
    scheme = build_condition_scheme(points)
    order = ["良好", "普通", "注意", "警戒", "異常"]

    ranks = [order.index(scheme.label_for(p)) for p in sorted(set(points))]

    assert ranks == sorted(ranks)


def test_apply_condition_scheme_relabels_and_keeps_missing_as_none():
    points = list(_points({0: 8, 1: 8, 2: 8})) + [None]
    df = pd.DataFrame(
        {"condition_points": points, "condition_label": ["固定"] * len(points)}
    )

    relabeled, scheme = apply_condition_scheme(df)

    assert scheme is not None
    assert pd.isna(relabeled["condition_label"].iloc[-1])  # pandas 2ではNone、3ではNaN
    assert set(relabeled["condition_label"].dropna()) <= {"良好", "普通", "注意", "警戒", "異常"}
    assert df["condition_label"].iloc[0] == "固定"  # 元のDataFrameは変えない


def test_apply_condition_scheme_falls_back_when_few_records():
    df = pd.DataFrame({"condition_points": [0.0, 1.0, 2.0], "condition_label": ["a", "b", "c"]})

    relabeled, scheme = apply_condition_scheme(df)

    assert scheme is None
    assert relabeled is df


def test_apply_condition_scheme_without_points_column():
    df = pd.DataFrame({"x": [1]})

    assert apply_condition_scheme(df) == (df, None) or apply_condition_scheme(df)[1] is None


@pytest.mark.parametrize("dtype_none", [True])
def test_all_none_points_object_column_is_handled(dtype_none):
    df = pd.DataFrame({"condition_points": [None] * 20, "condition_label": [None] * 20})

    _, scheme = apply_condition_scheme(df)

    assert scheme is None


# --- 点数の配置がまったく異なる人でも、破綻せず妥当に分けられること ---

_ORDER = ["良好", "普通", "注意", "警戒", "異常"]
_SCENARIOS = {
    "西村型": {0: 15, 1: 12, 2: 5, 3: 3, 4: 5, 7: 2, 13: 1, 20: 1},
    "0が8割で残りが7pt": {0: 32, 7: 8},
    "0〜10が一様": {i: 4 for i in range(11)},
    "全体に高い": {8: 6, 9: 8, 10: 9, 11: 7, 12: 5, 14: 3, 22: 1, 25: 1},
    "二極": {0: 12, 1: 8, 10: 8, 11: 7, 12: 5},
    "常に0": {0: 40},
    "常に6": {6: 40},
    "0と2のみ": {0: 25, 2: 15},
    "1〜2中心": {0: 3, 1: 20, 2: 15, 3: 2},
    "狭い範囲": {3: 15, 4: 15, 5: 10},
    "半分が20pt": {20: 20, 0: 10, 1: 6, 2: 4},
    "点数が多種類": {i: 2 for i in range(55)},
}


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_any_distribution_gives_valid_monotonic_labels(name):
    points = _points(_SCENARIOS[name])

    scheme = build_condition_scheme(points)

    assert scheme is not None
    labels = [scheme.label_for(p) for p in sorted(set(points))]
    assert all(label in _ORDER for label in labels)
    ranks = [_ORDER.index(label) for label in labels]
    assert ranks == sorted(ranks)  # 点数が高いほど、同じかより重い区分
    assert labels[0] == "良好"  # 最も低い点数は常に良好
    # 描画用の区切り文字列が、使った区分と一致する
    assert [label for label, _ in scheme.describe()] == [
        label for label in _ORDER if label in set(labels)
    ]


@pytest.mark.parametrize("name", list(_SCENARIOS))
def test_non_outlier_groups_are_never_empty(name):
    points = _points(_SCENARIOS[name])
    scheme = build_condition_scheme(points)

    counts = pd.Series([scheme.label_for(p) for p in points]).value_counts()

    for band in scheme.bands:
        assert counts.get(band.label, 0) > 0


@pytest.mark.parametrize(
    "name", ["0〜10が一様", "全体に高い", "点数が多種類", "二極", "西村型"]
)
def test_balance_is_reasonable_when_values_are_not_tied(name):
    points = _points(_SCENARIOS[name])
    scheme = build_condition_scheme(points)

    counts = pd.Series([scheme.label_for(p) for p in points]).value_counts()
    regular = [counts.get(label, 0) for label in _ORDER[:4]]

    # 点数の同点で分けられない分はあるが、最大の区分でも全体の半分以下に収まる。
    assert max(regular) <= 0.5 * sum(regular)


def test_all_same_value_is_good_but_exposes_high_best_band():
    scheme = build_condition_scheme(_points({6: 40}))

    assert scheme.label_for(6) == "良好"
    assert scheme.best_band_max_points == 6  # 良好に高い点数が含まれていることを画面で知らせる用


def test_best_band_max_points_is_low_for_a_typical_person():
    scheme = build_condition_scheme(_points({0: 15, 1: 9, 2: 5, 3: 3, 4: 5, 13: 1, 20: 1}))

    assert scheme.best_band_max_points == 0
