"""LLMを使わない、統計値と条件分岐だけによる即時の振り返りコメント生成。

ローカルLLM（qwen3:8b）はCPU環境では1回あたり数十秒〜数分かかることがあり、
支援員が日々グラフを確認する用途には不向き。そのため既定表示はこちらの
即時生成コメントを用いる（LLMによる詳しい解説は別途 llm_insight.generate_insight
をボタン操作で任意に呼び出す）。

文面はすべて事前に用意した定型パターンの組み合わせのみで構成されるため、
llm_insight.py と同じガードレール（医学的診断をしない・対話補助のトーンに限定する）を
構造的に満たす。
"""

from __future__ import annotations

import pandas as pd

from health_dashboard.formatting import format_axis_value
from health_dashboard.insight_context import build_context

# トレンドを「ほぼ横ばい」とみなす変化量のしきい値（各軸の単位そのまま）。
# 睡眠の質・気分は1〜3や1〜10程度のスケールのため0.5を、
# 中途覚醒回数は1回未満の変化を横ばい扱いとする。
_STABLE_THRESHOLD: dict[str, float] = {
    "bedtime_hours": 0.5,
    "wake_hours": 0.5,
    "quality_score": 0.5,
    "night_awakenings": 1.0,
    "mood_wake": 0.5,
    "mood_commute": 0.5,
}
_DEFAULT_STABLE_THRESHOLD = 0.5


def _trend_phrase(axis_col: str, trend_diff: float | None) -> str:
    if trend_diff is None:
        return ""
    threshold = _STABLE_THRESHOLD.get(axis_col, _DEFAULT_STABLE_THRESHOLD)
    if abs(trend_diff) < threshold:
        return "期間を通してほぼ横ばいの傾向です。"
    direction = "増える" if trend_diff > 0 else "減る"
    return f"期間の後半にかけて{direction}傾向が見られます。"


def generate_rule_based_insight(
    df: pd.DataFrame, axis_col: str, axis_label: str, period_label: str
) -> str:
    """統計値の条件分岐のみで、即時に振り返りコメントを生成する（LLM不使用）。"""
    ctx = build_context(df, axis_col, axis_label, period_label)

    if ctx.stats.count == 0:
        return f"{period_label}の{axis_label}データがありません。"

    latest_text = format_axis_value(axis_col, ctx.latest_value)
    mean_text = format_axis_value(axis_col, ctx.stats.mean)
    trend_text = _trend_phrase(axis_col, ctx.trend_diff)

    return (
        f"{period_label}の{axis_label}は、直近の値が{latest_text}、"
        f"期間の平均は{mean_text}程度です。{trend_text}"
        "面談の際に、この期間の様子を一緒に振り返ってみるとよいかもしれません。"
    )
