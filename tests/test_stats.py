import pandas as pd
import pytest

from health_dashboard.stats import correlation, correlation_matrix, summarize


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
