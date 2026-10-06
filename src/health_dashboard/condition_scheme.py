"""体調ポイントを、個人ごとに「各マーカーの日数がなるべく均等になる」ように5段階へ割り振る。

固定の点数基準（data_loader.condition_label_from_points）は、点数の付き方に個人差があると
特定のマーカーに日数が偏りやすい。そこで、その人の記録の分布から区切りを決め直す。

- 異常: 外れ値（箱ひげ図の考え方で Q3 + 1.5×IQR を超える点数。ただし5点未満は外れ値にしない）。
  外れ値は均等化の計算から除く。
- 良好・普通・注意・警戒: 外れ値を除いた日を、点数の低い順に4区分へ、日数がなるべく
  均等になるよう分ける。同じ点数の日は同じ区分に入るため、完全には揃わない。
  点数の種類が4つ未満なら、良好から順に必要な数だけ使う。

どの区分も「その人の中での相対的な位置」であり、点数そのものが高い人でも良好と判定されうる。
記録が少ない（MIN_RECORDED_DAYS未満）場合は、区切りを決めずに固定の基準へ戻す。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import pandas as pd

# 外れ値を除いた日を分けるラベル（点数の低い順）と、外れ値のラベル。
BALANCED_LABELS = ["良好", "普通", "注意", "警戒"]
OUTLIER_LABEL = "異常"

# 区切りを決めるのに必要な、体調の記録がある日数。
MIN_RECORDED_DAYS = 14
# 外れ値の判定（Q3 + この倍率 × IQR を超える点数）。
OUTLIER_IQR_MULTIPLIER = 1.5
# この点数未満は、他の日より高くても外れ値（異常）にはしない。
MIN_OUTLIER_POINTS = 5


@dataclass(frozen=True)
class Band:
    label: str
    low: float  # この点数以上がこの区分（先頭は -inf）
    min_points: float  # この区分に入った日の最小点数
    max_points: float  # この区分に入った日の最大点数


@dataclass(frozen=True)
class ConditionScheme:
    bands: tuple[Band, ...]  # 良好〜警戒のうち使う区分（点数の低い順）
    outlier_above: float | None  # この点数を超えたら異常。外れ値が無ければNone

    def label_for(self, points: float | None) -> str | None:
        if points is None or pd.isna(points):
            return None
        if self.outlier_above is not None and points > self.outlier_above:
            return OUTLIER_LABEL
        label = self.bands[0].label
        for band in self.bands:
            if points >= band.low:
                label = band.label
        return label

    @property
    def best_band_max_points(self) -> float:
        """「良好」の区分に含まれる最大の点数。高いほど、良好の中にも点数の高い日がある。"""
        return self.bands[0].max_points

    def describe(self) -> list[tuple[str, str]]:
        """凡例用の (ラベル, 点数の範囲の文字列)。"""
        parts = []
        for band in self.bands:
            lo, hi = band.min_points, band.max_points
            parts.append((band.label, f"{lo:g}pt" if lo == hi else f"{lo:g}〜{hi:g}pt"))
        if self.outlier_above is not None:
            parts.append((OUTLIER_LABEL, f"{math.floor(self.outlier_above) + 1}pt以上"))
        return parts


def _split_evenly(values: list[float], counts: list[int]) -> list[tuple[int, int]]:
    """点数の種類（昇順）ごとの日数を、日数がなるべく均等になるよう連続する区分に分ける。

    戻り値は各区分の (開始位置, 終了位置の次) 。区分の数は最大4（種類が少なければその数）。
    日数のばらつき（目標との差の二乗和）が同じなら、最後（最も点数が高い）の区分の日数が
    少ない分け方を選ぶ。警戒が不必要に多くならないようにするため。
    """
    groups = min(len(BALANCED_LABELS), len(values))
    total = sum(counts)
    target = total / groups
    best_key: tuple[float, int] | None = None
    best_cuts: tuple[int, ...] = ()
    for cuts in itertools.combinations(range(1, len(values)), groups - 1):
        edges = (0, *cuts, len(values))
        sizes = [sum(counts[a:b]) for a, b in itertools.pairwise(edges)]
        key = (sum((size - target) ** 2 for size in sizes), sizes[-1])
        if best_key is None or key < best_key:
            best_key, best_cuts = key, edges
    return list(itertools.pairwise(best_cuts))


def build_condition_scheme(points: pd.Series) -> ConditionScheme | None:
    """体調ポイントの分布から区切りを決める。記録が少ない・点数が無い場合はNone。"""
    values = pd.to_numeric(points, errors="coerce").dropna().astype(float)
    if len(values) < MIN_RECORDED_DAYS:
        return None

    q1, q3 = values.quantile([0.25, 0.75])
    fence = max(q3 + OUTLIER_IQR_MULTIPLIER * (q3 - q1), MIN_OUTLIER_POINTS - 1)
    outliers = values[values > fence]
    regular = values[values <= fence]
    if regular.empty:
        return None

    counts = regular.value_counts().sort_index()
    distinct = [float(v) for v in counts.index]
    ranges = _split_evenly(distinct, [int(c) for c in counts])

    bands = []
    for i, (start, end) in enumerate(ranges):
        bands.append(
            Band(
                label=BALANCED_LABELS[i],
                low=float("-inf") if i == 0 else distinct[start],
                min_points=distinct[start],
                max_points=distinct[end - 1],
            )
        )
    return ConditionScheme(tuple(bands), fence if not outliers.empty else None)


def apply_condition_scheme(df: pd.DataFrame) -> tuple[pd.DataFrame, ConditionScheme | None]:
    """日報データの体調ラベルを、個人ごとの区切りで付け直す。

    区切りを決められない場合（記録が少ない等）は、dfをそのまま（固定基準のラベルのまま）返す。
    """
    if "condition_points" not in df.columns:
        return df, None
    scheme = build_condition_scheme(df["condition_points"])
    if scheme is None:
        return df, None
    relabeled = df.copy()
    relabeled["condition_label"] = relabeled["condition_points"].map(scheme.label_for)
    return relabeled, scheme
