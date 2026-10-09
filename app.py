"""就労移行支援向け 体調・睡眠分析ダッシュボード（Streamlitエントリーポイント）。

すべての処理はローカルPC内で完結する（データの外部送信は一切行わない）。
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from health_dashboard import paths
from health_dashboard.attendance import weekly_attendance
from health_dashboard.border_analysis import (
    MIN_DAYS_BEYOND,
    MIN_RECORDED_DAYS,
    BadBorder,
    count_recorded_days,
    describe_border,
    find_bad_borders,
    heaviest_border_exceeded,
)
from health_dashboard.condition_scheme import (
    ConditionScheme,
    apply_condition_scheme,
)
from health_dashboard.data_loader import (
    AXIS_DIRECTION,
    AXIS_OPTIONS,
    CATEGORICAL_AXIS_COLUMNS,
    CONDITION_ABNORMAL_THRESHOLD,
    CONDITION_CAUTION_POINTS,
    CONDITION_WARNING_POINTS,
    MOOD_TYPE_CHOICES,
    WEATHER_AXIS_COLUMNS,
    attach_condition,
    filter_by_period,
    find_absence_file,
    find_user_files,
    list_user_dirs,
    load_absence_dates,
    load_absence_records,
    load_daily_reports,
    load_selfcare_points,
)
from health_dashboard.data_management import (
    DAILY,
    UPLOAD_KINDS,
    create_user,
    file_status,
    save_uploaded_file,
)
from health_dashboard.formatting import format_axis_value, format_clock
from health_dashboard.rule_based_insight import (
    HighlightPeriod,
    find_highlight_periods,
    generate_rule_based_insight,
    resolve_highlight_segments,
)
from health_dashboard.stats import (
    CorrelationPair,
    correlation_matrix,
    correlation_pairs_by_strength,
    summarize,
)
from health_dashboard.weather import (
    WEATHER_ORDER,
    attach_weather,
    fetch_weather,
    load_weather_location,
)

DATA_DIR = paths.data_dir()
WEATHER_CONFIG_PATH = paths.weather_config_path()
PERIOD_OPTIONS = ["直近1ヶ月", "全期間"]
DATA_MANAGEMENT_MODE = "データ管理"
ADD_USER_OPTION = "新しい利用者を追加"
DISPLAY_MODE_OPTIONS = ["グラフ", "出席状況", "相関表"]
SETTINGS_OPTIONS = [DATA_MANAGEMENT_MODE]

# 縦軸の選択肢にはないが、相関表には加える項目: (表示名, 列名)。
# 天気は 雨=0・曇=1・晴=2 に数値化した値、体調ポイントはセルフケアシート由来（高いほど不調）。
_CORRELATION_EXTRA_AXES = [
    ("天気（雨0・曇1・晴2）", "weather_score"),
    ("体調", "condition_points"),
]

_WEATHER_CORRELATION_COLUMNS = WEATHER_AXIS_COLUMNS | {"weather_score"}
# 睡眠時間は入眠時間・起床時間から計算した値で、この2つとの相関は当然高くなるため、
# 「結局何が言いたいの？」のまとめからは外す（表には表示する）。
_DERIVED_CORRELATION_PAIRS = {
    frozenset({"sleep_duration_hours", "bedtime_hours"}),
    frozenset({"sleep_duration_hours", "wake_hours"}),
}

# 相関係数の絶対値に対する強さの判定基準。
_CORRELATION_STRONG_THRESHOLD = 0.6
_CORRELATION_MODERATE_THRESHOLD = 0.3

# マーカー（○/△/×）はセルフケアシート由来の体調ポイントを表す（睡眠の質ではない）。
# 0pt=良好、1〜2pt=普通、3pt=注意、4pt=警戒、5pt以上=異常の5段階。
_CONDITION_STYLE = {
    "良好": {"symbol": "circle", "color": "#1565c0", "label": "○ 良い"},
    "普通": {"symbol": "triangle-up", "color": "#2e7d32", "label": "△ 普通"},
    "注意": {"symbol": "x", "color": "#fb8c00", "label": "× 注意"},
    "警戒": {"symbol": "x", "color": "#c62828", "label": "× 警戒"},
    "異常": {"symbol": "x", "color": "#000000", "label": "× 異常"},
}
# セルフケアシートに記録がない日（日報はあるがセルフケアの記入がない日）。
# 早退などにより記入できなかった可能性を示すサインとして、黒い□で表示する。
_CONDITION_DEFAULT_STYLE = {
    "symbol": "square-open",
    "color": "#000000",
    "label": "□ 記録なし（早退等の可能性）",
}

# 悪化ボーダーの横線の色。体調が×になる割合: 90%=赤、80%=黄、70%=青。
_BORDER_LINE_COLORS = {0.9: "#e53935", 0.8: "#f9a825", 0.7: "#1e88e5"}

# 振り返りコメントで言及している期間の背景色（薄い色）。
_HIGHLIGHT_STYLE = {
    "worst_stability": ("rgba(244, 67, 54, 0.14)", "薄い赤＝ばらつきが最も大きかった期間"),
    "best_level": ("rgba(76, 175, 80, 0.16)", "薄い緑＝最も良い状態だった期間"),
    "best_stability": ("rgba(33, 150, 243, 0.14)", "薄い青＝最も安定していた期間"),
}

# 欠席した日の縦線の色。精神的な理由=赤、体調の理由=黄、それ以外（通院など）=灰。
_ABSENCE_STYLE = {
    "mental": ("#e53935", "赤＝精神的な理由"),
    "physical": ("#fbc02d", "黄＝体調の理由"),
    "other": ("#9e9e9e", "灰＝その他（通院など）"),
}

_QUALITY_SCORE_TICKS = {1: "悪い", 2: "普通", 3: "良好"}


def _apply_time_axis_ticks(fig: go.Figure, values: list[float]) -> None:
    valid = [v for v in values if v is not None]
    if not valid:
        return
    lo, hi = min(valid), max(valid)
    step = 1 if (hi - lo) <= 14 else 2
    start = int(lo) - (int(lo) % step)
    tickvals = list(range(start, int(hi) + step + 1, step))
    ticktext = [format_clock(v) for v in tickvals]
    fig.update_yaxes(tickmode="array", tickvals=tickvals, ticktext=ticktext)


def _apply_date_axis_ticks(fig: go.Figure, dates: list) -> None:
    """横軸（日付）の目盛りを各月の1日・15日に固定する。

    表示期間が長い（「全期間」等）と、データ点の間隔に依存したPlotlyの既定の
    目盛りが不規則な間隔に見えてしまうため、月初・月半ばという分かりやすい
    区切りに揃える。該当日がほぼ無い短い期間では、目盛りが
    1つ以下になり不自然になるため既定の目盛りのままにする。
    """
    valid = [d for d in dates if d is not None]
    if not valid:
        return
    min_date, max_date = min(valid), max(valid)

    tickvals: list[dt.date] = []
    year, month = min_date.year, min_date.month
    while (year, month) <= (max_date.year, max_date.month):
        for day in (1, 15):
            candidate = dt.date(year, month, day)
            if min_date <= candidate <= max_date:
                tickvals.append(candidate)
        month += 1
        if month > 12:
            month = 1
            year += 1

    if len(tickvals) < 2:
        return

    ticktext = []
    for i, d in enumerate(tickvals):
        label = f"{d.month}/{d.day}"
        if i == 0 or (d.month == 1 and d.day == 1):
            label += f"<br>{d.year}"
        ticktext.append(label)

    fig.update_xaxes(tickmode="array", tickvals=tickvals, ticktext=ticktext)


@st.cache_data
def _load_data(path_str: str, mtime: float):
    return load_daily_reports(path_str)


@st.cache_data
def _load_absence_records(path_str: str, mtime: float):
    return load_absence_records(path_str)


@st.cache_data
def _load_absence(path_str: str, mtime: float):
    return load_absence_dates(path_str)


@st.cache_data
def _load_selfcare(path_str: str, mtime: float):
    return load_selfcare_points(path_str)


@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def _fetch_weather_cached(latitude: float, longitude: float, start: dt.date, end: dt.date):
    # 失敗時は例外を投げ、結果をキャッシュさせない（次回の操作で再取得できるようにする）。
    return fetch_weather(latitude, longitude, start, end)


def _attach_weather_to(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    """気象データを結合する。取得できなかった場合は、サイドバーに出す案内文も返す。"""
    location = load_weather_location(WEATHER_CONFIG_PATH)
    if location is None:
        return attach_weather(df, None), (
            "気圧・天気は未設定です。weather_config.json に施設の緯度・経度を"
            "書くと表示できます。"
        )
    try:
        weather_df = _fetch_weather_cached(*location, min(df["date"]), max(df["date"]))
    except Exception:
        return attach_weather(df, None), (
            "気象データを取得できませんでした。インターネット接続を確認してください。"
        )
    return attach_weather(df, weather_df), None


def load_source_dataframe(user_dir: Path | None):
    """利用者の日報・セルフケアを読み込む。日報が登録されていなければ (None, None)。"""
    daily_path, selfcare_path = find_user_files(user_dir) if user_dir else (None, None)
    if daily_path is None:
        return None, None

    df = _load_data(str(daily_path), daily_path.stat().st_mtime)

    if selfcare_path is not None:
        selfcare_df = _load_selfcare(str(selfcare_path), selfcare_path.stat().st_mtime)
        df = attach_condition(df, selfcare_df)
    else:
        st.info(
            "セルフケアシートがまだ登録されていません（左の「設定」の「データ管理」から"
            "アップロードできます）。"
            f"体調マーカーはすべて「{_CONDITION_DEFAULT_STYLE['label']}」として表示されます。"
        )
        df["condition_points"] = None
        df["condition_label"] = None
    return _attach_weather_to(df)


def _condition_hover_text(label, points) -> str:
    # pandas 3以降は、文字列の列の欠損が None でなく NaN になる。どちらでも「記録なし」にする。
    if label is None or pd.isna(label):
        return _CONDITION_DEFAULT_STYLE["label"]
    if points is None or pd.isna(points):
        return label
    return f"{label}（{int(points)}pt）"


def _expand_choices(df: pd.DataFrame, axis_col: str) -> pd.DataFrame:
    """複数選択の項目を、選択肢ごとに1行へ展開する（1日に複数選んだ日は複数行になる）。

    元の選択全体は表示用に「_selection_text」へ残す。選択のない日は行が無くなる。
    """
    expanded = df.copy()
    expanded["_selection_text"] = expanded[axis_col].map(
        lambda v: "、".join(v) if isinstance(v, list) else "―"
    )
    return expanded.explode(axis_col).dropna(subset=[axis_col]).reset_index(drop=True)


def build_figure(
    df,
    axis_label: str,
    axis_col: str,
    borders: list[BadBorder] | None = None,
    highlights: list[HighlightPeriod] | None = None,
    absences: list[tuple[dt.date, str]] | None = None,
) -> go.Figure:
    is_categorical = axis_col in CATEGORICAL_AXIS_COLUMNS
    if is_categorical:
        df = _expand_choices(df, axis_col)

    styles = [
        _CONDITION_STYLE.get(label, _CONDITION_DEFAULT_STYLE) for label in df["condition_label"]
    ]
    hover_condition = [
        _condition_hover_text(label, points)
        for label, points in zip(df["condition_label"], df["condition_points"], strict=False)
    ]

    hover_weather = [label if isinstance(label, str) else "―" for label in df["weather_label"]]

    # 選択式の軸は、折れ線にせずプロットのみにする。全選択肢を縦軸に並べるため、
    # 選択肢の位置（0始まり）を縦軸の値として使う。
    if is_categorical:
        positions = {choice: i for i, choice in enumerate(MOOD_TYPE_CHOICES)}
        y_values = df[axis_col].map(positions)
        hover_value = df["_selection_text"].tolist()
        value_template = "%{customdata[2]}"
    else:
        y_values = df[axis_col]
        hover_value = [""] * len(df)
        value_template = "%{y}"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=y_values,
            mode="markers" if is_categorical else "lines+markers",
            line=dict(color="#90a4ae"),
            marker=dict(
                size=11,
                symbol=[s["symbol"] for s in styles],
                color=[s["color"] for s in styles],
                line=dict(width=1, color="white"),
            ),
            customdata=list(zip(hover_condition, hover_weather, hover_value, strict=True)),
            hovertemplate=(
                f"%{{x|%-m/%-d}}<br>値: {value_template}<br>体調: %{{customdata[0]}}"
                "<br>天気: %{customdata[1]}<extra></extra>"
            ),
            name=axis_label,
        )
    )
    fig.update_layout(
        margin=dict(l=40, r=20, t=20, b=40),
        height=420,
        yaxis_title=axis_label,
        xaxis_title="日付",
        showlegend=False,
    )
    # 全期間表示（月初・月半ばの目盛り）と表記を揃えるため、既定の目盛り
    # （直近1ヶ月など）も月/日形式にする。
    fig.update_xaxes(tickformat="%-m/%-d")

    if axis_col in ("bedtime_hours", "wake_hours"):
        _apply_time_axis_ticks(fig, df[axis_col].dropna().tolist())
    elif is_categorical:
        fig.update_yaxes(
            tickmode="array",
            tickvals=list(range(len(MOOD_TYPE_CHOICES))),
            ticktext=MOOD_TYPE_CHOICES,
            range=[len(MOOD_TYPE_CHOICES) - 0.5, -0.5],  # 先頭の選択肢を上に置く
        )
    elif axis_col == "quality_score":
        fig.update_yaxes(
            tickmode="array",
            tickvals=list(_QUALITY_SCORE_TICKS.keys()),
            ticktext=list(_QUALITY_SCORE_TICKS.values()),
        )

    for segment in resolve_highlight_segments(highlights or []):
        # 日付の中心から前後半日ずつ広げ、1日だけの区間も見えるようにする。
        fig.add_vrect(
            x0=dt.datetime.combine(segment.start, dt.time()) - dt.timedelta(hours=12),
            x1=dt.datetime.combine(segment.end, dt.time()) + dt.timedelta(hours=12),
            fillcolor=_HIGHLIGHT_STYLE[segment.kind][0],
            line_width=0,
            layer="below",
        )

    for day, category in absences or []:
        fig.add_vline(
            x=dt.datetime.combine(day, dt.time()),
            line=dict(color=_ABSENCE_STYLE[category][0], width=2),
        )

    for border in borders or []:
        color = _BORDER_LINE_COLORS[border.min_rate]
        for i, y in enumerate(sorted(border.line_values, reverse=True)):
            line = dict(color=color, width=2, dash="dash")
            if i == 0:
                # 基準値の上下に2本引く場合も、名前は上の線にだけ付ける。
                fig.add_hline(
                    y=y,
                    line=line,
                    annotation_text=f"{border.min_rate * 100:.0f}%ボーダー",
                    annotation_position="top right",
                    annotation_font_color=color,
                )
            else:
                fig.add_hline(y=y, line=line)

    _apply_date_axis_ticks(fig, df["date"].tolist())
    return fig


def _condition_threshold_text(scheme: ConditionScheme | None) -> str:
    if scheme is None:
        return (
            f"0pt=良好、1〜{CONDITION_CAUTION_POINTS - 1}pt=普通、"
            f"{CONDITION_CAUTION_POINTS}pt=注意、{CONDITION_WARNING_POINTS}pt=警戒、"
            f"{CONDITION_ABNORMAL_THRESHOLD}pt以上=異常"
        )
    return "、".join(f"{points}={label}" for label, points in scheme.describe())


def render_condition_legend(scheme: ConditionScheme | None = None) -> None:
    order = ("良好", "普通", "注意", "警戒", "異常")
    # 「× 注意」「× 警戒」「× 異常」は記号が同じで色だけが違うため、マーカーと同じ色を
    # 凡例のテキストにも付けて区別できるようにする。
    legend_items = [
        f'<span style="color:{_CONDITION_STYLE[k]["color"]}">{_CONDITION_STYLE[k]["label"]}</span>'
        for k in order
    ]
    legend_items.append(
        f'<span style="color:{_CONDITION_DEFAULT_STYLE["color"]}">'
        f'{_CONDITION_DEFAULT_STYLE["label"]}</span>'
    )
    st.caption(
        "マーカー（体調・セルフケアシート由来）: "
        + "　".join(legend_items)
        + f"（{_condition_threshold_text(scheme)}。"
        + "セルフケアシートに記録がない日は早退等の可能性を示す黒い□で表示）",
        unsafe_allow_html=True,
    )


def render_absence_legend(absences: list[tuple[dt.date, str]]) -> None:
    used = {category for _, category in absences}
    items = [
        f'<span style="color:{color}; font-weight:600">{text}</span>'
        for category, (color, text) in _ABSENCE_STYLE.items()
        if category in used
    ]
    if items:
        st.caption("縦線は欠席した日: " + "　".join(items), unsafe_allow_html=True)


def render_highlight_legend(highlights: list[HighlightPeriod]) -> None:
    kinds = {segment.kind for segment in resolve_highlight_segments(highlights)}
    items = [
        f'<span style="background:{fill}; padding:0 6px">{text}</span>'
        for kind, (fill, text) in _HIGHLIGHT_STYLE.items()
        if kind in kinds
    ]
    if items:
        st.caption(
            "背景色（振り返りコメントで触れている期間）: " + "　".join(items),
            unsafe_allow_html=True,
        )


def render_border_legend() -> None:
    st.caption(
        "横線は悪化ボーダー（この人の体調が×になる割合）: "
        '<span style="color:#e53935">赤＝90%</span>　'
        '<span style="color:#f9a825">黄＝80%</span>　'
        '<span style="color:#1e88e5">青＝70%</span>',
        unsafe_allow_html=True,
    )


def render_border_panel(
    borders: list[BadBorder],
    axis_col: str,
    axis_label: str,
    latest_value: float | None,
    recorded_days: int,
    bad_days: int,
) -> None:
    """個人ごとの悪化ボーダーを、振り返りコメントとは独立して表示する。"""
    st.subheader("悪化ボーダー")
    st.caption(
        "この方の過去の記録（全期間）から、この項目がどの値になると体調が×（注意以上）に"
        "なりやすいかを探した結果です。記録が少ないうちは偏りが出やすいため、該当日数も"
        "あわせてご覧ください。医学的な基準ではありません。"
    )
    if not borders:
        if recorded_days < MIN_RECORDED_DAYS:
            st.caption(
                f"体調の記録がある日が{recorded_days}日で、ボーダーを調べるのに必要な"
                f"{MIN_RECORDED_DAYS}日に達していません。"
            )
        else:
            st.caption(
                f"体調の記録がある{recorded_days}日（うち×は{bad_days}日）を調べましたが、"
                f"超えた日が{MIN_DAYS_BEYOND}日以上あり、体調が×になる割合が70%以上になる"
                "ボーダーは見つかっていません。"
            )
        return
    for border in borders:
        show = {0.9: st.error, 0.8: st.warning, 0.7: st.info}[border.min_rate]
        show(describe_border(axis_col, axis_label, border))
    exceeded = heaviest_border_exceeded(borders, latest_value)
    if exceeded is not None:
        latest_text = format_axis_value(axis_col, latest_value)
        st.markdown(
            f"**直近の{axis_label}（{latest_text}）は、「{exceeded.report}」のボーダーを"
            "超えています。**"
        )


def render_stats(df, axis_col: str) -> None:
    s = summarize(df[axis_col])
    cols = st.columns(5)
    cols[0].metric("データ件数", s.count)
    cols[1].metric("平均", f"{s.mean:.2f}" if s.mean is not None else "―")
    cols[2].metric("中央値", f"{s.median:.2f}" if s.median is not None else "―")
    cols[3].metric("最頻値", f"{s.mode:.2f}" if s.mode is not None else "―")
    cols[4].metric("ばらつき（標準偏差）", f"{s.std:.2f}" if s.std is not None else "―")


def render_weather_breakdown(df, axis_col: str, axis_label: str) -> None:
    """選択中の項目を、天気（晴/曇/雨/雪）ごとに平均して並べる。"""
    grouped = df.groupby("weather_label")[axis_col].agg(["count", "mean"])
    grouped = grouped[grouped["count"] > 0]
    if grouped.empty:
        return
    rows = [
        {
            "天気": label,
            "日数": int(grouped.loc[label, "count"]),
            f"{axis_label}の平均": format_axis_value(axis_col, grouped.loc[label, "mean"]),
        }
        for label in WEATHER_ORDER
        if label in grouped.index
    ]
    st.subheader("天気ごとの平均")
    st.caption(
        "表示中の期間を、その日の天気で分けた平均です。日数が少ない天気は参考程度に"
        "ご覧ください。天気は気象データ上の区分で、あくまで傾向の確認用です。"
    )
    st.table(pd.DataFrame(rows).set_index("天気"))


def render_insight(
    df, axis_col: str, axis_label: str, period_label: str, borders: list[BadBorder]
) -> None:
    st.subheader("振り返りコメント")
    if axis_col in WEATHER_AXIS_COLUMNS:
        st.info(
            f"{axis_label}は利用者の状態ではなく天候の値のため、振り返りコメントは"
            "表示しません。睡眠や気分との関連は「相関表」で確認できます。"
        )
        return
    st.caption(
        "※ 医学的な診断は行いません。あくまで傾向の提示と、面談での対話のきっかけを"
        "提案するための参考コメントです。"
    )

    st.info(generate_rule_based_insight(df, axis_col, axis_label, period_label, borders))


def _correlation_cell_style(r: float) -> str:
    if pd.isna(r):
        return "background-color: #f5f5f5; color: #bdbdbd;"
    abs_r = abs(r)
    if abs_r >= _CORRELATION_STRONG_THRESHOLD:
        return "background-color: #ef5350; color: white;"
    if abs_r >= _CORRELATION_MODERATE_THRESHOLD:
        return "background-color: #fff176;"
    return "background-color: white;"


def _format_week_range(start: dt.date, end: dt.date) -> str:
    return f"{start.month}/{start.day}〜{end.month}/{end.day}"


def build_attendance_rate_figure(weekly: pd.DataFrame) -> go.Figure:
    """週ごとの出席率の推移（1週間＝1点）。"""
    weeks = [
        _format_week_range(start, end)
        for start, end in zip(weekly["week_start"], weekly["week_end"], strict=True)
    ]
    fig = go.Figure(
        go.Scatter(
            x=weekly["week_start"],
            y=weekly["attendance_rate"] * 100,
            mode="lines+markers",
            line=dict(color="#90a4ae"),
            marker=dict(size=11, color="#1565c0", line=dict(width=1, color="white")),
            customdata=list(
                zip(weeks, weekly["attended_days"], weekly["absent_days"], strict=True)
            ),
            hovertemplate=(
                "%{customdata[0]}（月〜金）<br>出席率: %{y:.0f}%"
                "<br>出席 %{customdata[1]}日／欠席 %{customdata[2]}日<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        margin=dict(l=40, r=20, t=20, b=40),
        height=360,
        yaxis_title="出席率（%）",
        xaxis_title="週の開始日（月曜）",
        showlegend=False,
    )
    fig.update_yaxes(range=[-5, 105], tickvals=[0, 25, 50, 75, 100])
    fig.update_xaxes(tickformat="%-m/%-d")
    return fig


def build_attendance_score_figure(weekly: pd.DataFrame) -> go.Figure:
    """週ごとの「出席日数×出席率」の推移（1週間＝1点）。満点は5。"""
    weeks = [
        _format_week_range(start, end)
        for start, end in zip(weekly["week_start"], weekly["week_end"], strict=True)
    ]
    fig = go.Figure(
        go.Scatter(
            x=weekly["week_start"],
            y=weekly["attendance_score"],
            mode="lines+markers",
            line=dict(color="#90a4ae"),
            marker=dict(size=11, color="#1565c0", line=dict(width=1, color="white")),
            customdata=list(
                zip(
                    weeks,
                    weekly["attended_days"],
                    weekly["absent_days"],
                    weekly["attendance_rate"] * 100,
                    strict=True,
                )
            ),
            hovertemplate=(
                "%{customdata[0]}（月〜金）<br>出席日数×出席率: %{y:.2f}"
                "<br>出席 %{customdata[1]}日／欠席 %{customdata[2]}日"
                "（出席率 %{customdata[3]:.0f}%）<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        margin=dict(l=40, r=20, t=20, b=40),
        height=360,
        yaxis_title="出席日数 × 出席率",
        xaxis_title="週の開始日（月曜）",
        showlegend=False,
    )
    fig.update_yaxes(range=[-0.25, 5.25], tickvals=[0, 1, 2, 3, 4, 5])
    fig.update_xaxes(tickformat="%-m/%-d")
    return fig


def _absences_in_range(user_dir: Path | None, df: pd.DataFrame) -> list[tuple[dt.date, str]]:
    """表示中のグラフの日付範囲にある欠席日（日報のある日は除く）を返す。"""
    absence_path = find_absence_file(user_dir) if user_dir else None
    if absence_path is None or df.empty:
        return []
    try:
        records = _load_absence_records(str(absence_path), absence_path.stat().st_mtime)
    except ValueError:
        return []
    reported = set(df["date"])
    start, end = min(df["date"]), max(df["date"])
    return [(d, c) for d, c in records if start <= d <= end and d not in reported]


def render_attendance(df_all: pd.DataFrame, user_dir: Path | None) -> None:
    st.subheader("出席状況")
    absence_path = find_absence_file(user_dir) if user_dir else None
    if absence_path is None:
        st.info(
            "欠席のデータ（ファイル名に「欠席」を含むExcel）が見つからないため、"
            "出席率を計算できません。利用者のフォルダに欠席のファイルを入れてください。"
        )
        return
    try:
        absent_dates = _load_absence(str(absence_path), absence_path.stat().st_mtime)
    except ValueError as error:
        st.warning(f"欠席のデータを読み込めませんでした: {error}")
        return

    weekly = weekly_attendance(df_all["date"], absent_dates)
    if weekly.empty:
        st.warning("1週間（月〜金）が終了した出席・欠席の記録がまだありません。")
        return

    st.caption(
        "1週間（月〜金）を1つのデータとして集計しています。出席日数は日報のある日、"
        "欠席日数は欠席フォームが提出された日（日報のある日を除く）で、出席率は"
        "「出席日数 ÷（出席日数 + 欠席日数）」です。日報も欠席連絡も無い日（休み・祝日など）は"
        "含みません。まだ終わっていない最新の週は、半端なデータのため含めていません。"
        "左のサイドバーの縦軸・期間の選択は、この表示には影響しません。"
    )
    st.markdown("#### 出席日数 × 出席率")
    st.caption(
        "出席日数に出席率を掛けた値です（満点は5）。欠席した週は、欠席の無い週より"
        "低くなります。例: 4日出席して欠席なしなら4.0、5日の予定で1日欠席した"
        "（4日出席・出席率80%）なら3.2。"
    )
    st.plotly_chart(build_attendance_score_figure(weekly), use_container_width=True)
    st.markdown("#### 出席率の推移")
    st.plotly_chart(build_attendance_rate_figure(weekly), use_container_width=True)


def _format_pair_lines(pairs: list[CorrelationPair], label_of: dict[str, str]) -> str:
    if not pairs:
        return "- 該当なし"
    return "\n".join(f"- {label_of[p.first]}　と　{label_of[p.second]}" for p in pairs)


def render_correlation_summary(
    pairs_by_strength: dict[str, list[CorrelationPair]], label_of: dict[str, str]
) -> None:
    """相関表の内容を、関係の強さごとの組み合わせとして文章で言い換える。"""
    st.subheader("結局何が言いたいの？")
    st.caption(
        "上の表を、関係の強さごとに言い換えたものです。睡眠時間と、入眠時間・起床時間の"
        "組み合わせは、計算で求めた値どうしのため除いています。データが足りない"
        "組み合わせも含みません。"
    )

    st.markdown("#### → 関係性が強いもの同士")
    st.markdown(_format_pair_lines(pairs_by_strength["strong"], label_of))
    st.markdown("#### → 少し関係があるもの同士")
    st.markdown(_format_pair_lines(pairs_by_strength["moderate"], label_of))

    weak = pairs_by_strength["weak"]
    st.markdown("#### → ほとんど関係がないもの同士")
    with st.expander(f"{len(weak)}組を表示"):
        st.markdown(_format_pair_lines(weak, label_of))


def render_correlation_table(df: pd.DataFrame, axes: list[tuple[str, str]]) -> None:
    st.subheader("相関表")
    st.caption(
        "すべてのデータ期間（全期間）を使って算出したピアソン相関係数です。"
        f"｜r｜が{_CORRELATION_STRONG_THRESHOLD}以上を赤（相関が強い）、"
        f"{_CORRELATION_MODERATE_THRESHOLD}〜{_CORRELATION_STRONG_THRESHOLD}を黄"
        "（相関がありそう）で示しています。左のサイドバーの縦軸・期間の選択は"
        "この表には影響しません。"
    )
    st.caption(
        "※ あくまで数値上の相関であり、因果関係を示すものではありません。"
        "医学的な判断は行わず、面談で気になる組み合わせを話題にする際の参考としてください。"
    )

    axis_labels = [label for label, _ in axes]
    axis_cols = [col for _, col in axes]
    if "weather_score" in axis_cols:
        st.caption(
            "天気は 雨=0・曇=1・晴=2 に置き換えて計算しています（雪の日は対象外）。"
            "数値が大きいほど天気が良い日です。気圧・天気どうしの組み合わせは対象外（―）です。"
        )
    if "condition_points" in axis_cols:
        st.caption(
            "体調はセルフケアシート由来の点数（体調ポイント）で、高いほど不調を表します。"
            "そのため、睡眠の質・気分などとは「マイナス」の相関が、"
            "中途覚醒回数とは「プラス」の相関が、悪い状態の連動を示します。"
        )

    matrix = correlation_matrix(df, axis_cols)
    # 気圧・天気どうしの相関は、利用者の状態を知る手がかりにならないため対象外（―）にする。
    weather_cols = [col for col in axis_cols if col in _WEATHER_CORRELATION_COLUMNS]
    matrix.loc[weather_cols, weather_cols] = float("nan")
    pairs_by_strength = correlation_pairs_by_strength(
        matrix,
        _CORRELATION_STRONG_THRESHOLD,
        _CORRELATION_MODERATE_THRESHOLD,
        exclude=_DERIVED_CORRELATION_PAIRS,
    )
    label_of = dict(zip(axis_cols, axis_labels, strict=True))
    matrix.index = axis_labels
    matrix.columns = axis_labels

    styled = matrix.style.map(_correlation_cell_style).format("{:.2f}", na_rep="―")
    # st.dataframe（対話型グリッド）はStylerのna_repを無視してNaNを"None"と
    # 表示してしまうため、Styler全体を静的HTMLとして描画するst.tableを使う。
    st.table(styled)

    render_correlation_summary(pairs_by_strength, label_of)


def _flash(message: str) -> None:
    st.session_state.setdefault("_flash", []).append(message)


def _show_flash_messages() -> None:
    for message in st.session_state.pop("_flash", []):
        st.success(message)


def render_add_user_page() -> None:
    """利用者のプルダウンで「新しい利用者を追加」を選んだときの画面。"""
    st.subheader("新しい利用者を追加する")
    st.caption(
        "利用者の名前を入れて追加します。追加すると、その利用者が選ばれた状態で、"
        "日報などをアップロードする画面に移ります。"
    )
    if not list_user_dirs(DATA_DIR):
        st.info("まだ利用者がいません。最初の利用者を追加してください。")
    with st.form("add_user_form", clear_on_submit=True):
        name = st.text_input("利用者の名前")
        submitted = st.form_submit_button("利用者を追加")
    if not submitted:
        return
    created, error = create_user(DATA_DIR, name)
    if error is not None:
        st.error(error)
        return
    _flash(f"「{created.name}」さんを追加しました。続けて、日報などをアップロードしてください。")
    st.session_state["_pending_user"] = created
    st.session_state["_open_data_management"] = True
    st.rerun()


def render_upload_row(user_dir: Path, kind, registered) -> None:
    if registered is None:
        st.markdown(f"**{kind.label}**　未登録")
    else:
        path, modified = registered
        st.markdown(
            f"**{kind.label}**　登録済み（{path.name}、"
            f"{modified.month}/{modified.day} {modified:%H:%M} 更新）"
        )
    counter = st.session_state.get(f"_upload_counter_{user_dir.name}_{kind.key}", 0)
    uploaded = st.file_uploader(
        f"{kind.label}のExcelファイル（.xlsx）を選ぶ"
        + ("　※ 今のファイルと置き換わります" if registered else ""),
        type=["xlsx"],
        key=f"upload_{user_dir.name}_{kind.key}_{counter}",
    )
    if uploaded is None:
        return
    error = save_uploaded_file(user_dir, kind, uploaded.getvalue())
    if error is not None:
        st.error(error)
        return
    _flash(f"「{user_dir.name}」さんの{kind.label}を登録しました。")
    # アップロード欄を空に戻す（キーを変える）。取り込み後は同じ画面にとどまる。
    st.session_state[f"_upload_counter_{user_dir.name}_{kind.key}"] = counter + 1
    st.session_state["_open_data_management"] = True
    st.rerun()


def render_data_management(user_dir: Path) -> None:
    st.subheader("データ管理")
    st.caption(
        "選択中の利用者の、日報・セルフケアシート・欠席情報（Excel形式）を"
        "アップロードします。同じ種類のファイルをもう一度アップロードすると、古いファイルは"
        "新しいファイルに置き換わります。"
    )
    _show_flash_messages()

    st.markdown(f"#### 「{user_dir.name}」さんのデータをアップロードする")
    st.caption(
        "対象の利用者は、左の「利用者」で切り替えられます。新しい利用者は、"
        "「利用者」の「新しい利用者を追加」から追加できます。"
    )
    status = file_status(user_dir)
    if status[DAILY.key] is None:
        st.warning("日報がまだ登録されていません。日報をアップロードすると、グラフが表示されます。")
    for kind in UPLOAD_KINDS:
        render_upload_row(user_dir, kind, status[kind.key])
        st.write("")
    st.caption(
        "欠席情報は、欠席フォームの回答（Excel）です。無い利用者は、アップロードしなくても"
        "グラフは表示されます（出席率は、欠席情報があるときだけ計算できます）。"
    )


def _show_page(
    settings: str | None = None, display: bool = False, settings_selected: bool = False
) -> None:
    """「表示」と「設定」のどちらか一方だけを選ばれた状態にする。

    - settings を渡す: その設定の画面を開く（「表示」の選択は外す）。
    - display=True（「表示」を選んだときの処理）: 「設定」の選択を外す。
    - settings_selected=True（「設定」を選んだときの処理）: 「表示」の選択を外す。
    """
    if settings is not None:
        st.session_state["settings_mode"] = settings
        st.session_state["display_mode"] = None
    elif display:
        st.session_state["settings_mode"] = None
    elif settings_selected:
        st.session_state["display_mode"] = None


def _ensure_page_state() -> None:
    """最初の表示は「グラフ」。両方とも未選択になっていたら、「グラフ」に戻す。"""
    if st.session_state.get("display_mode") is None and (
        st.session_state.get("settings_mode") is None
    ):
        st.session_state["display_mode"] = DISPLAY_MODE_OPTIONS[0]


def render_quit_button() -> None:
    """配布版（ダブルクリック起動）で、アプリを終了するボタンを出す。"""
    if st.button("アプリを終了する"):
        st.success("終了しています。このタブ（ウィンドウ）は閉じて構いません。")
        # 画面にメッセージを返してから、サーバーを止める。
        threading.Timer(1.5, os._exit, args=(0,)).start()
        st.stop()


def main() -> None:
    st.set_page_config(page_title="体調・睡眠分析ダッシュボード", layout="wide")
    st.title("体調・睡眠分析ダッシュボード")
    st.caption(
        "利用者の日報・セルフケアのデータはローカルPC内で処理し、外部には送信しません。"
        "気象データを設定した場合のみ、施設の位置（緯度経度）と日付範囲を気象API"
        "（Open-Meteo）に送信して気圧・天気を取得します。"
    )

    # 利用者の追加・アップロードの直後に、選択中の利用者や表示を切り替えるための予約。
    # ウィジェットを作る前でないと値を変えられないため、ここで反映する。
    pending_user = st.session_state.pop("_pending_user", None)
    if pending_user is not None:
        st.session_state["selected_user"] = pending_user
    if st.session_state.pop("_open_data_management", False):
        _show_page(settings=DATA_MANAGEMENT_MODE)
    _ensure_page_state()

    # 利用者のプルダウンは、既存の利用者の最後に「新しい利用者を追加」を置く。
    # 利用者が1人もいないときは、これだけが並ぶので、最初から追加の画面になる。
    user_dirs = list_user_dirs(DATA_DIR)
    user_options = [*user_dirs, ADD_USER_OPTION]
    if st.session_state.get("selected_user") not in user_options:
        st.session_state.pop("selected_user", None)
    with st.sidebar:
        st.header("表示設定")
        selected = st.selectbox(
            "利用者",
            user_options,
            format_func=lambda o: o if o == ADD_USER_OPTION else o.name,
            key="selected_user",
        )

    if selected == ADD_USER_OPTION:
        if paths.launched_by_launcher():
            with st.sidebar:
                render_quit_button()
        render_add_user_page()
        return
    user_dir = selected

    df_all, weather_notice = load_source_dataframe(user_dir)
    if df_all is None:
        # 選んだ利用者の日報がまだ無いときは、アップロードの画面だけ出す。
        if paths.launched_by_launcher():
            with st.sidebar:
                render_quit_button()
        render_data_management(user_dir)
        return

    # 気象データが無い場合は、気象の軸を選択肢・相関表から外す。
    axes = [(label, col) for label, col in AXIS_OPTIONS if df_all[col].notna().any()]

    with st.sidebar:
        axis_label = st.radio("縦軸（表示する項目）", [label for label, _ in axes])
        axis_col = dict(axes)[axis_label]
        period_label = st.radio("期間", PERIOD_OPTIONS)
        # 「表示」と「設定」は別のグループ。どちらか一方だけが選ばれた状態にする
        # （片方を選ぶと、もう片方の選択は外れる）。
        st.radio(
            "表示",
            DISPLAY_MODE_OPTIONS,
            index=None,
            key="display_mode",
            on_change=_show_page,
            kwargs={"display": True},
        )
        st.radio(
            "設定",
            SETTINGS_OPTIONS,
            index=None,
            key="settings_mode",
            on_change=_show_page,
            kwargs={"settings_selected": True},
        )
        display_mode = st.session_state["display_mode"]
        settings_mode = st.session_state["settings_mode"]
        if weather_notice:
            st.caption(weather_notice)
        if paths.launched_by_launcher():
            render_quit_button()

    if settings_mode == DATA_MANAGEMENT_MODE:
        render_data_management(user_dir)
        return

    df_all, condition_scheme = apply_condition_scheme(df_all)
    if condition_scheme is None:
        st.sidebar.caption(
            "体調の記録が少ないため、マーカーの区切りを自動で決められません。"
            "目安の点数基準を使います。"
        )

    if display_mode == "出席状況":
        render_attendance(df_all, user_dir)
        return

    if display_mode == "相関表":
        numeric_axes = [(label, col) for label, col in axes if col not in CATEGORICAL_AXIS_COLUMNS]
        correlation_axes = numeric_axes + [
            (label, col) for label, col in _CORRELATION_EXTRA_AXES if df_all[col].notna().any()
        ]
        render_correlation_table(df_all, correlation_axes)
        return

    df = filter_by_period(df_all, period_label)

    if df.empty:
        st.warning("選択した期間にデータがありません。")
        return

    borders = []
    if axis_col not in CATEGORICAL_AXIS_COLUMNS:
        borders = find_bad_borders(df_all, axis_col, AXIS_DIRECTION.get(axis_col, "none"))

    highlights = []
    if axis_col not in CATEGORICAL_AXIS_COLUMNS | WEATHER_AXIS_COLUMNS:
        highlights = find_highlight_periods(df, axis_col, period_label)

    absences = _absences_in_range(user_dir, df)

    fig = build_figure(df, axis_label, axis_col, borders, highlights, absences)
    st.plotly_chart(fig, use_container_width=True)
    render_condition_legend(condition_scheme)
    if (
        condition_scheme is not None
        and condition_scheme.best_band_max_points >= CONDITION_CAUTION_POINTS
    ):
        st.caption(
            "※ この方は、点数が全体的に高めのため、「良好」の区分にも"
            f"{condition_scheme.best_band_max_points:g}ptの日が含まれています"
            f"（固定の基準では{CONDITION_CAUTION_POINTS}pt以上は「注意」以上）。"
            "点数そのものは、グラフのホバー表示や相関表の「体調」で確認できます。"
        )
    render_absence_legend(absences)
    render_highlight_legend(highlights)
    if borders:
        render_border_legend()

    if axis_col in CATEGORICAL_AXIS_COLUMNS:
        st.caption(
            f"{axis_label}は選択式の項目のため、平均などの統計と振り返りコメントは"
            "表示しません。"
        )
        return

    render_stats(df, axis_col)
    if axis_col not in WEATHER_AXIS_COLUMNS:
        render_weather_breakdown(df, axis_col, axis_label)
    st.divider()
    latest_values = df_all[axis_col].dropna()
    latest_value = float(latest_values.iloc[-1]) if not latest_values.empty else None
    recorded_days, bad_days = count_recorded_days(df_all, axis_col)
    render_border_panel(borders, axis_col, axis_label, latest_value, recorded_days, bad_days)
    render_insight(df, axis_col, axis_label, period_label, borders)


if __name__ == "__main__":
    main()
