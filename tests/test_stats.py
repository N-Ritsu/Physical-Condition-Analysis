import pandas as pd
import pytest

from health_dashboard.stats import correlation, summarize


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
