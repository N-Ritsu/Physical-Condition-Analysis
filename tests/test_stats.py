import pandas as pd
import pytest

from health_dashboard.stats import (
    correlation,
    correlation_matrix,
    correlation_pairs_by_strength,
    summarize,
)


def test_summarize_basic():
    s = pd.Series([1, 2, 2, 3, None])
    result = summarize(s)
    assert result.count == 4
    assert result.mean == pytest.approx(2.0)
    assert result.median == 2.0
    assert result.mode == 2.0
    assert result.std == pytest.approx(s.dropna().std())


def test_summarize_empty():
    result = summarize(pd.Series([None, None], dtype=float))
    assert result.count == 0
    assert result.mean is None
    assert result.median is None
    assert result.mode is None
    assert result.std is None


def test_summarize_single_value_has_no_std():
    result = summarize(pd.Series([5]))
    assert result.count == 1
    assert result.mean == 5.0
    assert result.std is None


def test_correlation():
    df = pd.DataFrame({"a": [1, 2, 3, 4], "b": [2, 4, 6, 8]})
    assert correlation(df, "a", "b") == pytest.approx(1.0)


def test_correlation_insufficient_data():
    df = pd.DataFrame({"a": [1, None], "b": [None, 2]})
    assert correlation(df, "a", "b") is None


def test_correlation_matrix_values_and_symmetry():
    df = pd.DataFrame(
        {
            "a": [1, 2, 3, 4],
            "b": [2, 4, 6, 8],  # aと完全な正の相関
            "c": [4, 3, 2, 1],  # aと完全な負の相関
        }
    )
    matrix = correlation_matrix(df, ["a", "b", "c"])

    assert matrix.loc["a", "b"] == pytest.approx(1.0)
    assert matrix.loc["b", "a"] == pytest.approx(1.0)
    assert matrix.loc["a", "c"] == pytest.approx(-1.0)


def test_correlation_matrix_diagonal_is_nan():
    df = pd.DataFrame({"a": [1, 2, 3], "b": [3, 2, 1]})
    matrix = correlation_matrix(df, ["a", "b"])

    assert pd.isna(matrix.loc["a", "a"])
    assert pd.isna(matrix.loc["b", "b"])


def test_correlation_matrix_insufficient_data_is_nan():
    df = pd.DataFrame({"a": [1, None], "b": [None, 2]})
    matrix = correlation_matrix(df, ["a", "b"])

    assert pd.isna(matrix.loc["a", "b"])


def _matrix():
    names = ["a", "b", "c", "d"]
    values = {
        ("a", "b"): 0.9,  # 強い
        ("a", "c"): -0.7,  # 強い（負の相関）
        ("a", "d"): 0.4,  # 中程度
        ("b", "c"): -0.35,  # 中程度（負の相関）
        ("b", "d"): 0.1,  # ほぼ無関係
        ("c", "d"): float("nan"),  # データ不足
    }
    matrix = pd.DataFrame(float("nan"), index=names, columns=names)
    for (x, y), v in values.items():
        matrix.loc[x, y] = v
        matrix.loc[y, x] = v
    return matrix


def test_correlation_pairs_grouped_and_sorted_by_strength():
    groups = correlation_pairs_by_strength(_matrix(), 0.6, 0.3)

    assert [(p.first, p.second) for p in groups["strong"]] == [("a", "b"), ("a", "c")]
    assert [(p.first, p.second) for p in groups["moderate"]] == [("a", "d"), ("b", "c")]
    assert [(p.first, p.second) for p in groups["weak"]] == [("b", "d")]


def test_correlation_pairs_skip_nan_and_each_pair_once():
    groups = correlation_pairs_by_strength(_matrix(), 0.6, 0.3)

    all_pairs = [frozenset({p.first, p.second}) for g in groups.values() for p in g]
    assert frozenset({"c", "d"}) not in all_pairs  # NaNは除く
    assert len(all_pairs) == len(set(all_pairs)) == 5


def test_correlation_pairs_exclude():
    groups = correlation_pairs_by_strength(_matrix(), 0.6, 0.3, exclude={frozenset({"a", "b"})})

    assert [(p.first, p.second) for p in groups["strong"]] == [("a", "c")]


def test_correlation_pairs_boundary_values_go_to_stronger_group():
    matrix = pd.DataFrame(
        [[float("nan"), 0.6, 0.3], [0.6, float("nan"), 0.29], [0.3, 0.29, float("nan")]],
        index=["a", "b", "c"],
        columns=["a", "b", "c"],
    )

    groups = correlation_pairs_by_strength(matrix, 0.6, 0.3)

    assert [(p.first, p.second) for p in groups["strong"]] == [("a", "b")]
    assert [(p.first, p.second) for p in groups["moderate"]] == [("a", "c")]
    assert [(p.first, p.second) for p in groups["weak"]] == [("b", "c")]
