"""ルールベース／LLMどちらの解説生成でも使う、統計サマリーの共通コンテキスト。"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from health_dashboard.stats import SummaryStats, summarize


@dataclass
class InsightContext:
    axis_label: str
    period_label: str
    stats: SummaryStats
    latest_value: float | None
    trend_diff: float | None


def build_context(
    df: pd.DataFrame, axis_col: str, axis_label: str, period_label: str
) -> InsightContext:
    """選択中の指標について、平均・直近値・前半後半のトレンドをまとめる。"""
    stats = summarize(df[axis_col])
    valid = df.dropna(subset=[axis_col])
    latest_value = float(valid.iloc[-1][axis_col]) if not valid.empty else None

    trend_diff = None
    if len(valid) >= 4:
        half = len(valid) // 2
        first_mean = valid.iloc[:half][axis_col].mean()
        second_mean = valid.iloc[half:][axis_col].mean()
        if pd.notna(first_mean) and pd.notna(second_mean):
            trend_diff = float(second_mean - first_mean)

    return InsightContext(
        axis_label=axis_label,
        period_label=period_label,
        stats=stats,
        latest_value=latest_value,
        trend_diff=trend_diff,
    )
