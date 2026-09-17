"""統計値と条件分岐だけによる、即時の振り返りコメント生成（ローカルLLMは不使用）。

当初はローカルLLM（Ollama + qwen3:8b）による考察生成もあわせて実装していたが、
以下の理由から条件分岐のみの方式に一本化した（経緯は
docs/05_prototype_creation_progress.md 参照）。
- 支援員の中にAIの出力そのものへの不安感を持つ人がいた。
- GPUのないPCでは生成に数十秒〜数分かかり、日々グラフを確認する運用に不向きだった。
- 条件分岐だけでも、トレンド・外れ値・安定度・水準比較まで踏み込んだ分析ができ、
  実用上十分な精度が得られた。

文面はすべて事前に用意した定型パターンの組み合わせのみで構成されるため、
ガードレール（医学的診断をしない・対話補助のトーンに限定する）が構造的に保証される。

含まれる分析:
- 期間全体の平均・直近値
- 線形回帰によるトレンド（軸の「良し悪しの方向性」を踏まえた評価付き）
- 直近値の外れ値検知（zスコア）
- 体調（セルフケアシート由来）が「注意」以上の状態が続いている場合のストリーク検知
- 期間内の安定度比較（全期間なら4週間単位、直近1か月なら1週間単位でローリング集計し、
  最も安定していた期間・最も不安定だった期間を検出。直近の期間と比較する）。
  ウィンドウ間の差が小さい場合でも、最も安定していたウィンドウ自体のばらつきが
  大きければ「期間を通して常に不安定」と判定する（睡眠の質のように取りうる値の幅が
  狭い軸では、常にジグザグしていても窓ごとの差だけを見ると小さくなり、誤って
  「安定」と判定されてしまうため）。
- 期間内の水準（良し悪し）比較。安定度比較と同じウィンドウを使い、軸の「良し悪しの
  方向性」（AXIS_DIRECTION）を踏まえて、最も値が良かった期間・悪かった期間を検出する。
  安定していたかどうかと、値そのものが良かったかどうかは別の観点であるため、
  安定度比較とは別の文で言及する（例: 中途覚醒回数が「安定していた」期間が、
  必ずしも「回数が少なかった」期間とは限らない）。方向性のない軸（入眠時間・起床時間）
  では評価しない。
- 「直近1週間」表示は期間内比較ができるほどデータがないため、直近2日分の値を
  軸ごとの絶対的な良し悪し基準（トレンドや平均とは無関係）で評価する。

締めの一文は上記の分析結果に応じて3パターンに出し分ける。
- 直近に悪化傾向・外れ値・体調ストリーク・ばらつき（不安定期への接近、または常に
  不安定な状態）・悪い水準の期間への接近・直近2日の絶対的な悪化のいずれかがあれば注意喚起
- 直近は問題ないが期間内の安定期と不安定期、または良かった時期と悪かった時期の差が
  大きければ、比較による自己分析を促す
- どちらもなければ、期間を通して安定している旨を伝える
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from health_dashboard.data_loader import AXIS_DIRECTION
from health_dashboard.formatting import format_axis_value
from health_dashboard.insight_context import build_context

# トレンドを「ほぼ横ばい」とみなす変化量のしきい値（各軸の単位そのまま）。
# 睡眠の質・気分は1〜3や1〜10程度のスケールのため0.5を、
# 中途覚醒回数は1回未満の変化を横ばい扱いとする。
# 安定度比較・水準比較（_analyze_period）でも「有意な差」の判定に流用する。
_STABLE_THRESHOLD: dict[str, float] = {
    "bedtime_hours": 0.5,
    "wake_hours": 0.5,
    "quality_score": 0.5,
    "night_awakenings": 1.0,
    "mood_wake": 0.5,
    "mood_commute": 0.5,
}
_DEFAULT_STABLE_THRESHOLD = 0.5

# 回帰の決定係数（R^2）がこれ未満の場合、トレンドはあっても
# 日によるばらつきが大きく参考程度である旨を付け加える。
_TREND_CONFIDENCE_R2 = 0.3

# 直近値がこの標準偏差の倍数を超えて平均から離れている場合、外れ値として言及する。
_OUTLIER_Z_THRESHOLD = 2.0

# 体調（セルフケアシート由来）のストリーク検知。
_CONDITION_SEVERITY = {"良好": 0, "普通": 1, "注意": 2, "警戒": 3, "異常": 4}
_CONDITION_STREAK_MIN_SEVERITY = _CONDITION_SEVERITY["注意"]
_CONDITION_STREAK_MIN_LENGTH = 3

# 安定度・水準比較のウィンドウ幅（日数）。
_YEAR_WINDOW_DAYS = 28  # 「全期間」表示: 4週間を1つの単位とする
_MONTH_WINDOW_DAYS = 7  # 「直近1ヶ月」表示: 1週間を1つの単位とする
_YEAR_WINDOW_MIN_POINTS = 3
_MONTH_WINDOW_MIN_POINTS = 2

# 「直近1週間」表示で、期間内比較の代わりに使う直近日数と絶対評価の基準。
_RECENT_DAYS_FOR_ABSOLUTE_CHECK = 2
# 軸ごとの「悪い」と判定する絶対的な基準値。方向性のない軸（入眠時間・起床時間）は対象外。
# "higher"方向の軸は「この値未満は悪い」、"lower"方向の軸は「この値以上は悪い」という
# 意味で扱う（_is_bad_value参照）。
# quality_score: 2未満＝「悪い」カテゴリ（1）のみが該当。
# night_awakenings: 元データが「中途覚醒３以上」を最悪カテゴリとして扱っているのに
# 合わせ3回以上。
# mood_wake/mood_commute: 1〜10スケールの中間（5）未満。
_ABSOLUTE_BAD_THRESHOLD: dict[str, float] = {
    "quality_score": 2,
    "night_awakenings": 3,
    "mood_wake": 5,
    "mood_commute": 5,
}


def _format_date_jp(d: dt.date) -> str:
    return f"{d.month}月{d.day}日"


def _trend_phrase(
    axis_col: str, trend_diff: float | None, trend_r_squared: float | None
) -> tuple[str, bool]:
    """トレンドの説明文と、それが「注意したい方向」の変化かどうかを返す。"""
    if trend_diff is None:
        return "", False

    threshold = _STABLE_THRESHOLD.get(axis_col, _DEFAULT_STABLE_THRESHOLD)
    if abs(trend_diff) < threshold:
        return "期間を通してほぼ横ばいの傾向です。", False

    increasing = trend_diff > 0
    change_word = "増える" if increasing else "減る"
    direction = AXIS_DIRECTION.get(axis_col, "none")

    is_concerning = False
    if direction == "none":
        sentence = f"期間の後半にかけて{change_word}傾向が見られます。"
    else:
        is_good_direction = (direction == "higher") == increasing
        is_concerning = not is_good_direction
        evaluation = "良い方向" if is_good_direction else "注意したい方向"
        sentence = f"期間の後半にかけて{change_word}傾向が見られ、{evaluation}の変化です。"

    if trend_r_squared is not None and trend_r_squared < _TREND_CONFIDENCE_R2:
        sentence += "ただし日によるばらつきも大きく、目安としてご覧ください。"

    return sentence, is_concerning


def _zscore(latest_value: float | None, mean: float | None, std: float | None) -> float | None:
    if latest_value is None or mean is None or std is None or std == 0:
        return None
    return (latest_value - mean) / std


def _outlier_phrase(axis_label: str, z: float | None) -> str:
    if z is None or abs(z) < _OUTLIER_Z_THRESHOLD:
        return ""
    direction = "高い" if z > 0 else "低い"
    return f"直近の{axis_label}は、これまでの傾向と比べてかなり{direction}値になっています。"


def _is_concerning_outlier(axis_col: str, z: float | None) -> bool:
    """外れ値が「悪化方向」かどうかを軸の評価方向を踏まえて判定する。"""
    if z is None or abs(z) < _OUTLIER_Z_THRESHOLD:
        return False
    direction = AXIS_DIRECTION.get(axis_col, "none")
    if direction == "none":
        return True  # 方向性のない軸では、外れていること自体を注意対象とする
    if direction == "higher":
        return z < 0
    return z > 0  # direction == "lower"


def _condition_streak_length(df: pd.DataFrame) -> int:
    """体調（セルフケアシート由来）が「注意」以上の状態が続いている記録件数を返す。

    選択中の軸に関わらず、体調の見落とし防止という観点から常に評価する。
    """
    if "condition_label" not in df.columns:
        return 0
    labels = df.sort_values("date")["condition_label"].tolist()

    streak = 0
    for label in reversed(labels):
        if _CONDITION_SEVERITY.get(label, -1) >= _CONDITION_STREAK_MIN_SEVERITY:
            streak += 1
        else:
            break
    return streak


def _is_bad_value(axis_col: str, value: float | None) -> bool:
    """軸ごとの絶対基準（過去の平均などとは無関係）で、値が「悪い」と言えるかを判定する。

    方向性のない軸（入眠時間・起床時間）は評価対象外（常にFalse）。
    """
    if value is None:
        return False
    threshold = _ABSOLUTE_BAD_THRESHOLD.get(axis_col)
    if threshold is None:
        return False
    direction = AXIS_DIRECTION.get(axis_col, "none")
    if direction == "lower":
        return value >= threshold
    if direction == "higher":
        return value < threshold
    return False


def _recent_days_absolute_warning(df: pd.DataFrame, axis_col: str) -> bool:
    """直近数日の値を軸ごとの絶対基準で評価し、悪い値が含まれていればTrue。

    「直近1週間」表示のように期間内比較ができるだけのデータがない場合に使う。
    """
    valid = df.dropna(subset=[axis_col]).sort_values("date")
    if valid.empty:
        return False
    recent_values = valid[axis_col].tail(_RECENT_DAYS_FOR_ABSOLUTE_CHECK).tolist()
    return any(_is_bad_value(axis_col, v) for v in recent_values)


def _recent_absolute_phrase(axis_label: str, triggered: bool) -> str:
    if not triggered:
        return ""
    n = _RECENT_DAYS_FOR_ABSOLUTE_CHECK
    return f"直近{n}日の{axis_label}に、注意したい値が含まれています。"


@dataclass
class _PeriodAnalysis:
    period_noun: str
    recent_noun: str
    recent: dict | None

    # 安定度（ばらつき）の比較。
    # "large_gap"（安定期と不安定期の差が大きい）/
    # "uniform_volatile"（最も安定していた時期ですら変動が大きい＝常に不安定）/
    # "stable"（期間を通して落ち着いている）
    stability_pattern: str
    stability_best: dict
    stability_worst: dict
    recent_close_to_worst_stability: bool | None
    # 直近ウィンドウ自体の標準偏差が、軸のしきい値を単独で超えているか。
    recent_is_volatile: bool

    # 水準（良し悪し）の比較。方向性のある軸（AXIS_DIRECTION != "none"）のみ。
    level_best: dict | None
    level_worst: dict | None
    level_gap_is_large: bool
    recent_close_to_worst_level: bool | None


def _tiled_windows(
    valid: pd.DataFrame, axis_col: str, window_days: int, min_points: int
) -> list[dict]:
    """データ期間の先頭から window_days 日ごとに区切った非重複ウィンドウの統計を返す。"""
    if valid.empty:
        return []
    start_date = valid["date"].min()
    end_date = valid["date"].max()

    windows = []
    cursor = start_date
    while cursor <= end_date:
        window_end = cursor + dt.timedelta(days=window_days - 1)
        chunk = valid[(valid["date"] >= cursor) & (valid["date"] <= window_end)]
        if len(chunk) >= min_points:
            windows.append(
                {
                    "start": cursor,
                    "end": min(window_end, end_date),
                    "mean": float(chunk[axis_col].mean()),
                    "std": float(chunk[axis_col].std()),
                }
            )
        cursor = window_end + dt.timedelta(days=1)
    return windows


def _recent_window(
    valid: pd.DataFrame, axis_col: str, window_days: int, min_points: int
) -> dict | None:
    """期間末尾（直近）window_days日分の統計を返す。データが不足していればNone。"""
    if valid.empty:
        return None
    end_date = valid["date"].max()
    start_date = end_date - dt.timedelta(days=window_days - 1)
    chunk = valid[valid["date"] >= start_date]
    if len(chunk) < min_points:
        return None
    return {
        "mean": float(chunk[axis_col].mean()),
        "std": float(chunk[axis_col].std()),
    }


def _analyze_period(df: pd.DataFrame, axis_col: str, period_label: str) -> _PeriodAnalysis | None:
    """期間内の安定度・水準を比較する。

    「全期間」表示では4週間単位、「直近1ヶ月」表示では1週間単位でローリング集計する。
    「直近1週間」表示や、データ不足でウィンドウを2つ以上作れない場合はNoneを返す。
    """
    if period_label not in ("全期間", "直近1ヶ月"):
        return None

    is_year_view = period_label == "全期間"
    window_days = _YEAR_WINDOW_DAYS if is_year_view else _MONTH_WINDOW_DAYS
    min_points = _YEAR_WINDOW_MIN_POINTS if is_year_view else _MONTH_WINDOW_MIN_POINTS
    period_noun = "この期間" if is_year_view else "この1か月間"
    recent_noun = "1か月" if is_year_view else "1週間"

    valid = df.dropna(subset=[axis_col])
    windows = _tiled_windows(valid, axis_col, window_days, min_points)
    if len(windows) < 2:
        return None

    threshold = _STABLE_THRESHOLD.get(axis_col, _DEFAULT_STABLE_THRESHOLD)
    recent = _recent_window(valid, axis_col, window_days, min_points)

    # --- 安定度（ばらつき）の比較 ---
    stability_best = min(windows, key=lambda w: w["std"])
    stability_worst = max(windows, key=lambda w: w["std"])
    is_large_gap = (stability_worst["std"] - stability_best["std"]) >= threshold
    if is_large_gap:
        stability_pattern = "large_gap"
    elif stability_best["std"] >= threshold:
        # 窓ごとの差は小さいが、最も落ち着いていた窓ですら変動が大きい
        # ＝期間を通して常にばらつきが大きい状態。
        stability_pattern = "uniform_volatile"
    else:
        stability_pattern = "stable"

    recent_close_to_worst_stability = None
    if recent is not None and is_large_gap:
        dist_to_worst = abs(recent["std"] - stability_worst["std"])
        dist_to_best = abs(recent["std"] - stability_best["std"])
        recent_close_to_worst_stability = dist_to_worst < dist_to_best

    recent_is_volatile = recent is not None and recent["std"] >= threshold

    # --- 水準（良し悪し）の比較。方向性のある軸のみ。 ---
    direction = AXIS_DIRECTION.get(axis_col, "none")
    level_best = level_worst = None
    level_gap_is_large = False
    recent_close_to_worst_level = None
    if direction != "none":
        if direction == "lower":
            level_best = min(windows, key=lambda w: w["mean"])
            level_worst = max(windows, key=lambda w: w["mean"])
        else:  # "higher"
            level_best = max(windows, key=lambda w: w["mean"])
            level_worst = min(windows, key=lambda w: w["mean"])
        level_gap_is_large = abs(level_worst["mean"] - level_best["mean"]) >= threshold

        if recent is not None and level_gap_is_large:
            dist_to_worst = abs(recent["mean"] - level_worst["mean"])
            dist_to_best = abs(recent["mean"] - level_best["mean"])
            recent_close_to_worst_level = dist_to_worst < dist_to_best

    return _PeriodAnalysis(
        period_noun=period_noun,
        recent_noun=recent_noun,
        recent=recent,
        stability_pattern=stability_pattern,
        stability_best=stability_best,
        stability_worst=stability_worst,
        recent_close_to_worst_stability=recent_close_to_worst_stability,
        recent_is_volatile=recent_is_volatile,
        level_best=level_best,
        level_worst=level_worst,
        level_gap_is_large=level_gap_is_large,
        recent_close_to_worst_level=recent_close_to_worst_level,
    )


def _stability_window_phrase(
    analysis: _PeriodAnalysis | None, axis_col: str, axis_label: str
) -> tuple[str, str]:
    """安定度比較の説明文を (概要文, 直近との比較文) のタプルで返す。

    2つに分けているのは、間に水準比較の文（_level_window_phrase）を挟んで
    出力するため。「どの期間が安定していたか／不安定だったか」の説明の直後に
    「どの期間の値が良かったか／悪かったか」の説明を続け、直近との比較は
    その後にまとめて述べたほうが読みやすい。
    """
    if analysis is None:
        return "", ""

    if analysis.stability_pattern == "stable":
        main = (
            f"{analysis.period_noun}を通して、{axis_label}の安定度に大きな違いは"
            "見られませんでした。"
        )
        recent_sentence = ""
        if analysis.recent is not None:
            recent_mean_text = format_axis_value(axis_col, analysis.recent["mean"])
            if analysis.recent_is_volatile:
                # 過去の窓同士の差は小さいが、直近だけは単独でばらつきが大きい場合、
                # 「同様に落ち着いている」と言うと矛盾するため言い分ける。
                recent_sentence = (
                    f"ただし直近{analysis.recent_noun}は平均{recent_mean_text}で、"
                    "これまでよりばらつきが大きくなっています。"
                )
            else:
                recent_sentence = (
                    f"直近{analysis.recent_noun}も平均{recent_mean_text}で、同様に落ち着いています。"
                )
        return main, recent_sentence

    if analysis.stability_pattern == "uniform_volatile":
        main = (
            f"{analysis.period_noun}を通して、{axis_label}に落ち着いた時期は見られず、"
            "常にある程度のばらつきが続いています。"
        )
        recent_sentence = ""
        if analysis.recent is not None:
            recent_mean_text = format_axis_value(axis_col, analysis.recent["mean"])
            recent_sentence = (
                f"直近{analysis.recent_noun}も平均{recent_mean_text}で、同様の状態です。"
            )
        return main, recent_sentence

    # stability_pattern == "large_gap"
    best_start = _format_date_jp(analysis.stability_best["start"])
    best_end = _format_date_jp(analysis.stability_best["end"])
    worst_start = _format_date_jp(analysis.stability_worst["start"])
    worst_end = _format_date_jp(analysis.stability_worst["end"])
    best_range = f"{best_start}〜{best_end}"
    worst_range = f"{worst_start}〜{worst_end}"
    main = f"{best_range}が最も安定し、{worst_range}にばらつきが最も大きくなっていました。"

    recent_sentence = ""
    if analysis.recent is not None:
        recent_mean_text = format_axis_value(axis_col, analysis.recent["mean"])
        if analysis.recent_close_to_worst_stability:
            note = "ばらつきが大きかった時期に近い状態です。"
        elif analysis.recent_is_volatile:
            # 相対的には安定期に近いが、直近単独で見てもばらつきは大きい状態。
            note = "直近もばらつきが大きい状態です。"
        else:
            note = "安定していた時期に近い落ち着き具合です。"
        recent_sentence = f"直近{analysis.recent_noun}は平均{recent_mean_text}で、{note}"

    return main, recent_sentence


def _level_window_phrase(analysis: _PeriodAnalysis | None, axis_col: str, axis_label: str) -> str:
    """期間内で最も値が良かった期間・悪かった期間を、安定度とは別の観点で言及する。

    「最も安定していた期間」が、必ずしも「値が最も良かった期間」とは限らないため
    （例: 中途覚醒回数が一定して2回だった期間は安定はしているが、0回の期間より良いとは
    言えない）、安定度比較（_stability_window_phrase）とは別の文として出力する。
    """
    if analysis is None or analysis.level_best is None or not analysis.level_gap_is_large:
        return ""

    best_start = _format_date_jp(analysis.level_best["start"])
    best_end = _format_date_jp(analysis.level_best["end"])
    worst_start = _format_date_jp(analysis.level_worst["start"])
    worst_end = _format_date_jp(analysis.level_worst["end"])
    best_range = f"{best_start}〜{best_end}"
    worst_range = f"{worst_start}〜{worst_end}"
    best_text = format_axis_value(axis_col, analysis.level_best["mean"])
    worst_text = format_axis_value(axis_col, analysis.level_worst["mean"])

    return (
        f"また、{best_range}は{axis_label}が最も良い状態（平均{best_text}）で、"
        f"{worst_range}は注意したい状態（平均{worst_text}）でした。"
    )


def _closing_phrase(
    axis_col: str,
    axis_label: str,
    trend_concerning: bool,
    outlier_z: float | None,
    condition_streak_active: bool,
    period_analysis: _PeriodAnalysis | None,
    recent_absolute_bad: bool,
) -> str:
    """分析結果に応じて締めの一文を出し分ける。

    1. 直近に悪化傾向・外れ値・体調ストリーク・ばらつき（不安定期への接近、または
       期間を通して常に不安定）・悪い水準の期間への接近・直近数日の絶対的な悪化の
       いずれかがあれば注意喚起。
    2. 直近は問題ないが、期間内の安定期と不安定期、または良かった時期と悪かった時期の
       差が大きい場合は、比較による自己分析を促す。
    3. どちらでもなければ、期間を通して安定している旨を伝える。
    """
    concerning_outlier = _is_concerning_outlier(axis_col, outlier_z)
    recent_unstable = bool(period_analysis and period_analysis.recent_close_to_worst_stability)
    recent_is_volatile = bool(period_analysis and period_analysis.recent_is_volatile)
    recent_bad_level = bool(period_analysis and period_analysis.recent_close_to_worst_level)

    is_concerning = (
        trend_concerning
        or concerning_outlier
        or condition_streak_active
        or recent_unstable
        or recent_is_volatile
        or recent_bad_level
        or recent_absolute_bad
    )
    if is_concerning:
        return (
            f"直近の{axis_label}には気になる変化が見られるため、"
            "気にかけてあげたほうが良いかもしれません。"
        )

    self_analysis_trigger = bool(
        period_analysis
        and (
            period_analysis.stability_pattern == "large_gap"
            or period_analysis.level_gap_is_large
        )
    )
    if self_analysis_trigger:
        return (
            "時期によって落ち着いている時期と不安定な時期、または良かった時期と悪かった"
            "時期がはっきり分かれているため、それぞれの期間での生活リズムを比較すると、"
            "重要な要素が見つかるかもしれません。"
        )

    return f"{axis_label}はこの期間を通しておおむね安定しており、大きな問題は見られなさそうです。"


def generate_rule_based_insight(
    df: pd.DataFrame, axis_col: str, axis_label: str, period_label: str
) -> str:
    """統計値の条件分岐のみで、即時に振り返りコメントを生成する（LLM不使用）。"""
    ctx = build_context(df, axis_col, axis_label, period_label)

    if ctx.stats.count == 0:
        return f"{period_label}の{axis_label}データがありません。"

    latest_text = format_axis_value(axis_col, ctx.latest_value)
    mean_text = format_axis_value(axis_col, ctx.stats.mean)

    sentences = [
        f"{period_label}の{axis_label}は、直近の値が{latest_text}、"
        f"期間の平均は{mean_text}程度です。"
    ]

    trend_sentence, trend_concerning = _trend_phrase(axis_col, ctx.trend_diff, ctx.trend_r_squared)
    if trend_sentence:
        sentences.append(trend_sentence)

    z = _zscore(ctx.latest_value, ctx.stats.mean, ctx.stats.std)
    outlier_sentence = _outlier_phrase(axis_label, z)
    if outlier_sentence:
        sentences.append(outlier_sentence)

    streak_length = _condition_streak_length(df)
    condition_streak_active = streak_length >= _CONDITION_STREAK_MIN_LENGTH
    if condition_streak_active:
        sentences.append(f"直近の記録{streak_length}件連続で、体調が「注意」以上の状態になっています。")

    # 安定度比較（概要）→ 水準比較 → 安定度比較（直近との比較）の順で並べる。
    # 「どの期間が安定/不安定だったか」の直後に「どの期間の値が良かった/悪かったか」を
    # 続け、直近との比較はまとめて最後に述べたほうが読みやすいため。
    period_analysis = _analyze_period(df, axis_col, period_label)
    stability_main, stability_recent = _stability_window_phrase(
        period_analysis, axis_col, axis_label
    )
    if stability_main:
        sentences.append(stability_main)

    level_sentence = _level_window_phrase(period_analysis, axis_col, axis_label)
    if level_sentence:
        sentences.append(level_sentence)

    if stability_recent:
        sentences.append(stability_recent)

    recent_absolute_bad = False
    if period_label == "直近1週間":
        recent_absolute_bad = _recent_days_absolute_warning(df, axis_col)
        absolute_sentence = _recent_absolute_phrase(axis_label, recent_absolute_bad)
        if absolute_sentence:
            sentences.append(absolute_sentence)

    closing = _closing_phrase(
        axis_col,
        axis_label,
        trend_concerning,
        z,
        condition_streak_active,
        period_analysis,
        recent_absolute_bad,
    )
    sentences.append(closing)

    return "".join(sentences)
