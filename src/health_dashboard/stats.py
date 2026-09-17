"""選択期間・選択項目に対する統計計算（平均・中央値・最頻値・標準偏差・相関）。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class SummaryStats:
    mean: float | None
    median: float | None
    mode: float | None
    std: float | None
    count: int


def summarize(series: pd.Series) -> SummaryStats:
    """欠損値を除いた平均・中央値・最頻値・標準偏差を算出する。データがなければNoneを返す。"""
    valid = series.dropna()
    if valid.empty:
        return SummaryStats(mean=None, median=None, mode=None, std=None, count=0)
    mode_values = valid.mode()
    return SummaryStats(
        mean=float(valid.mean()),
        median=float(valid.median()),
        mode=float(mode_values.iloc[0]) if not mode_values.empty else None,
        std=float(valid.std()) if len(valid) >= 2 else None,
        count=int(valid.count()),
    )


def correlation(df: pd.DataFrame, col_a: str, col_b: str) -> float | None:
    """2列間のピアソン相関係数を算出する。両方に有効な値がある行が2件未満ならNone。"""
    paired = df[[col_a, col_b]].dropna()
    if len(paired) < 2:
        return None
    corr = paired[col_a].corr(paired[col_b])
    return float(corr) if pd.notna(corr) else None


def correlation_matrix(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """指定した列同士のピアソン相関係数行列を算出する。

    対角線（自分自身との相関）は常に自明（1.0）で比較の意味を持たないため、
    表示上「対象外」として扱えるようNaNにする。データ不足で算出できないペアもNaN。
    """
    matrix = pd.DataFrame(index=columns, columns=columns, dtype=float)
    for row in columns:
        for col in columns:
            matrix.loc[row, col] = float("nan") if row == col else correlation(df, row, col)
    return matrix
