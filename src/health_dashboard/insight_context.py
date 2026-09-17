"""振り返りコメント生成（rule_based_insight）で使う、統計サマリーの共通コンテキスト。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from health_dashboard.stats import SummaryStats, summarize


@dataclass
class InsightContext:
    axis_label: str
    period_label: str
    stats: SummaryStats
    latest_value: float | None
    trend_diff: float | None
    trend_r_squared: float | None


def build_context(
    df: pd.DataFrame, axis_col: str, axis_label: str, period_label: str
) -> InsightContext:
    """選択中の指標について、平均・直近値・回帰トレンドをまとめる。

    trend_diff は単純な前半後半平均の差ではなく、線形回帰の傾きに期間の日数（データ点数-1）
    を掛けた「期間を通した推定変化量」。外れ値1点に引っ張られにくく、直近値だけを見るより
    頑健なトレンド判定ができる。trend_r_squared は回帰の当てはまりの良さ（0〜1、高いほど
    傾向がはっきりしている）で、ばらつきが大きい場合の判定に使う。
    """
    stats = summarize(df[axis_col])
    valid = df.dropna(subset=[axis_col])
    latest_value = float(valid.iloc[-1][axis_col]) if not valid.empty else None

    trend_diff = None
    trend_r_squared = None
    if len(valid) >= 4:
        x = np.arange(len(valid), dtype=float)
        y = valid[axis_col].to_numpy(dtype=float)
        if np.ptp(y) > 0:
            slope, _intercept = np.polyfit(x, y, 1)
            trend_diff = float(slope * (len(valid) - 1))
            trend_r_squared = float(np.corrcoef(x, y)[0, 1] ** 2)
        else:
            # 全期間で値が一定の場合、回帰は自明（変化なし・完全な当てはまり）。
            trend_diff = 0.0
            trend_r_squared = 1.0

    return InsightContext(
        axis_label=axis_label,
        period_label=period_label,
        stats=stats,
        latest_value=latest_value,
        trend_diff=trend_diff,
        trend_r_squared=trend_r_squared,
    )
