"""個人ごとの「悪化ボーダー」の検出。

ある項目の値がどこを超える（または下回る）と、その人の体調が×（注意以上）になる
割合が一定以上になるかを、その人自身の過去の記録から探す。

- 割合が90%以上: 極めて危険な報告
- 割合が80%以上: 危険な報告
- 割合が70%以上: 注意すべき報告

医学的な基準ではなく、あくまでその人の過去の記録における傾向。記録が少ないうちは
偶然による偏りが大きいため、該当日数が少ないボーダーは採用しない（MIN_DAYS_BEYOND）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from health_dashboard.data_loader import QUALITY_SCORE_LABELS
from health_dashboard.formatting import format_clock

# (体調×になる割合の下限, 報告の名称)。厳しい順。
BORDER_LEVELS: list[tuple[float, str]] = [
    (0.9, "極めて危険な報告"),
    (0.8, "危険な報告"),
    (0.7, "注意すべき報告"),
]

# 体調が「×」（注意以上）のラベル。グラフの×マーカーと同じ。
BAD_CONDITION_LABELS = frozenset({"注意", "警戒", "異常"})

# ボーダーを超えた日数がこれ未満なら、偶然の可能性が高いため採用しない。
MIN_DAYS_BEYOND = 4
# 体調の記録がある日数がこれ未満なら、ボーダー分析自体を行わない。
MIN_RECORDED_DAYS = 10
# ボーダーを超えた日の×の割合が、超えていない日の割合よりこれ以上高いことを求める
# （もともと×が多い人で、どの値でも当てはまってしまうのを避けるため）。
MIN_LIFT = 0.3


# 「ある値に近いほど良い」項目の、基準となる値。この値からの離れ具合（上下どちらでも）で
# ボーダーを調べる。気圧の前日差は、0（前日と同じ）に近いほど良く、大きく上がっても
# 大きく下がっても悪い、として扱う。
BORDER_CENTER: dict[str, float] = {"pressure_change_hpa": 0.0}


@dataclass(frozen=True)
class BadBorder:
    report: str  # BORDER_LEVELSの報告名称
    min_rate: float  # このボーダーが満たした割合の下限（0.9など）
    side: str  # "high"（この値以上）/ "low"（この値以下）/ "far"（centerから上下どちらでも離れた）
    threshold: float  # sideがfarのときは、centerからの距離
    days_beyond: int
    bad_days: int
    center: float = 0.0  # sideがfarのときの基準値

    @property
    def rate(self) -> float:
        return self.bad_days / self.days_beyond

    def is_beyond(self, value: float | None) -> bool:
        if value is None or pd.isna(value):
            return False
        if self.side == "far":
            return abs(value - self.center) >= self.threshold
        return value >= self.threshold if self.side == "high" else value <= self.threshold

    @property
    def line_values(self) -> list[float]:
        """グラフに横線を引く位置。farは基準値の上下2本。"""
        if self.side == "far":
            return [self.center - self.threshold, self.center + self.threshold]
        return [self.threshold]


def count_recorded_days(df: pd.DataFrame, axis_col: str) -> tuple[int, int]:
    """ボーダー分析の対象になる日数と、そのうち体調が×の日数を返す。"""
    if "condition_label" not in df.columns:
        return 0, 0
    recorded = df.dropna(subset=[axis_col, "condition_label"])
    return len(recorded), int(recorded["condition_label"].isin(BAD_CONDITION_LABELS).sum())


def _sides_for(direction: str) -> list[str]:
    if direction == "lower":  # 少ないほど良い項目は、多い側が悪い
        return ["high"]
    if direction == "higher":  # 多いほど良い項目は、少ない側が悪い
        return ["low"]
    return ["high", "low"]


def _candidates(values: pd.Series, bad: pd.Series, side: str) -> list[tuple[float, int, int]]:
    """(しきい値, 超えた日数, そのうち×の日数) のうち、採用条件を満たすものを返す。"""
    total = len(values)
    total_bad = int(bad.sum())
    found = []
    for threshold in sorted(values.unique()):
        beyond = values >= threshold if side == "high" else values <= threshold
        days_beyond = int(beyond.sum())
        if days_beyond < MIN_DAYS_BEYOND or days_beyond == total:
            continue
        bad_beyond = int(bad[beyond].sum())
        outside_rate = (total_bad - bad_beyond) / (total - days_beyond)
        if bad_beyond / days_beyond - outside_rate < MIN_LIFT:
            continue
        found.append((float(threshold), days_beyond, bad_beyond))
    return found


def find_bad_borders(df: pd.DataFrame, axis_col: str, direction: str) -> list[BadBorder]:
    """項目ごとの悪化ボーダーを、報告の重い順に返す。

    各報告について、その割合を満たす中で最も手前（該当日数が最も多い）のしきい値を選ぶ。
    同じボーダーが複数の報告を満たす場合は、最も重い報告だけを返す。
    体調の記録が少ない・条件を満たすボーダーが無い場合は空リスト。
    """
    if "condition_label" not in df.columns:
        return []
    recorded = df.dropna(subset=[axis_col, "condition_label"])
    if len(recorded) < MIN_RECORDED_DAYS:
        return []

    values = recorded[axis_col].astype(float)
    bad = recorded["condition_label"].isin(BAD_CONDITION_LABELS)

    center = BORDER_CENTER.get(axis_col)
    if center is not None:
        # 基準値からの距離で、上下どちら向きの離れ方も合わせて調べる。
        candidates = [
            ("far", threshold, days, bad_days)
            for threshold, days, bad_days in _candidates((values - center).abs(), bad, "high")
            if threshold > 0
        ]
    else:
        candidates = [
            (side, threshold, days, bad_days)
            for side in _sides_for(direction)
            for threshold, days, bad_days in _candidates(values, bad, side)
        ]

    borders: list[BadBorder] = []
    seen: set[tuple[str, float]] = set()
    for min_rate, report in BORDER_LEVELS:
        qualified = [c for c in candidates if c[3] / c[2] >= min_rate]
        if not qualified:
            continue
        side, threshold, days, bad_days = max(qualified, key=lambda c: (c[2], c[3] / c[2]))
        if (side, threshold) in seen:
            continue
        seen.add((side, threshold))
        borders.append(
            BadBorder(report, min_rate, side, threshold, days, bad_days, center or 0.0)
        )
    return borders


_TIME_AXES = ("bedtime_hours", "wake_hours")


def describe_condition(axis_col: str, axis_label: str, border: BadBorder) -> str:
    """ボーダーを「中途覚醒回数が3回以上の日」のような言い回しにする。"""
    is_high = border.side == "high"
    value = border.threshold
    if border.side == "far" and axis_col == "pressure_change_hpa":
        return f"{axis_label}が前日から上下に{value:.1f}hPa以上動いた日"
    if axis_col == "quality_score":
        label = QUALITY_SCORE_LABELS.get(round(value), f"{value:g}")
        if not is_high and round(value) == 1:
            return f"{axis_label}が「{label}」の日"
        return f"{axis_label}が「{label}」{'以上' if is_high else '以下'}の日"
    if axis_col in _TIME_AXES:
        return f"{axis_label}が{format_clock(value)}{'以降' if is_high else '以前'}の日"
    if axis_col == "night_awakenings":
        text = f"{value:g}回"
    elif axis_col == "sleep_duration_hours":
        text = f"{value:.1f}時間"
    elif axis_col == "pressure_hpa":
        text = f"{value:.1f}hPa"
    elif axis_col == "pressure_change_hpa":
        text = f"{value:+.1f}hPa"
    else:
        text = f"{value:g}"
    return f"{axis_label}が{text}{'以上' if is_high else '以下'}の日"


def describe_border(axis_col: str, axis_label: str, border: BadBorder) -> str:
    """ボーダー1つ分の説明文（該当日数つき）。"""
    return (
        f"【{border.report}】{describe_condition(axis_col, axis_label, border)}は、"
        f"体調が×（注意以上）になる割合が{border.rate * 100:.0f}%"
        f"（{border.days_beyond}日中{border.bad_days}日）でした。"
    )


def heaviest_border_exceeded(
    borders: list[BadBorder], value: float | None
) -> BadBorder | None:
    """valueがボーダーを超えているものがあれば、最も重い報告のものを返す。"""
    return next((b for b in borders if b.is_beyond(value)), None)
