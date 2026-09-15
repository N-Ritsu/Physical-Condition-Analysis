"""グラフ・解説文で共通して使う、軸ごとの数値表示フォーマット。"""

from __future__ import annotations

import pandas as pd

from health_dashboard.data_loader import QUALITY_SCORE_LABELS

_TIME_AXES = ("bedtime_hours", "wake_hours")


def format_clock(hours: float) -> str:
    """連続数値軸（例: 24時以降は+24した値）を HH:MM 表記に戻す。"""
    hours_mod = hours % 24
    h = int(hours_mod)
    m = int(round((hours_mod - h) * 60))
    if m == 60:
        m = 0
        h = (h + 1) % 24
    return f"{h:02d}:{m:02d}"


def format_axis_value(axis_col: str, value: float | None) -> str:
    """指定した軸の値を、文章・表示に使いやすい形式の文字列にする。"""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "―"
    if axis_col in _TIME_AXES:
        return format_clock(value)
    if axis_col == "quality_score":
        return QUALITY_SCORE_LABELS.get(round(value), f"{value:.1f}")
    if axis_col == "night_awakenings":
        return f"{value:.1f}回"
    return f"{value:.1f}"
