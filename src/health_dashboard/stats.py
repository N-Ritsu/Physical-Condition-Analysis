"""選択期間・選択項目に対する統計計算（平均・中央値・最頻値・相関）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class SummaryStats:
    mean: float | None
    median: float | None
    mode: float | None
    count: int


def summarize(series: pd.Series) -> SummaryStats:
    """欠損値を除いた平均・中央値・最頻値を算出する。データがなければNoneを返す。"""
    valid = series.dropna()
    if valid.empty:
        return SummaryStats(mean=None, median=None, mode=None, count=0)
    mode_values = valid.mode()
    return SummaryStats(
        mean=float(valid.mean()),
        median=float(valid.median()),
        mode=float(mode_values.iloc[0]) if not mode_values.empty else None,
        count=int(valid.count()),
    )


def correlation(df: pd.DataFrame, col_a: str, col_b: str) -> float | None:
    """2列間のピアソン相関係数を算出する。両方に有効な値がある行が2件未満ならNone。"""
    paired = df[[col_a, col_b]].dropna()
    if len(paired) < 2:
        return None
    corr = paired[col_a].corr(paired[col_b])
    return float(corr) if pd.notna(corr) else None
