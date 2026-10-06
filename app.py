"""就労移行支援向け 体調・睡眠分析ダッシュボード（Streamlitエントリーポイント）。

すべての処理はローカルPC内で完結する（データの外部送信は一切行わない）。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

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
    load_daily_reports,
    load_selfcare_points,
)
from health_dashboard.formatting import format_axis_value, format_clock
from health_dashboard.rule_based_insight import generate_rule_based_insight
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

DATA_DIR = Path(__file__).parent / "data"
WEATHER_CONFIG_PATH = Path(__file__).parent / "weather_config.json"
PERIOD_OPTIONS = ["直近1週間", "直近1ヶ月", "全期間"]
DISPLAY_MODE_OPTIONS = ["グラフ", "出席状況", "相関表"]

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
    "異常": {"symbol": "x", "color": "#6a1b9a", "label": "× 異常"},
}
# セルフケアシートに記録がない日（日報はあるがセルフケアの記入がない日）。
# 早退などにより記入できなかった可能性を示すサインとして、黒い×で表示する。
_CONDITION_DEFAULT_STYLE = {
    "symbol": "x",
    "color": "#000000",
    "label": "× 記録なし（早退等の可能性）",
}

# 悪化ボーダーの横線の色。体調が×になる割合: 90%=赤、80%=黄、70%=青。
_BORDER_LINE_COLORS = {0.9: "#e53935", 0.8: "#f9a825", 0.7: "#1e88e5"}

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
    区切りに揃える。該当日がほぼ無い短い期間（直近1週間等）では、目盛りが
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
    daily_path, selfcare_path = find_user_files(user_dir) if user_dir else (None, None)

    if daily_path is not None:
        df = _load_data(str(daily_path), daily_path.stat().st_mtime)
    else:
        location = user_dir if user_dir else DATA_DIR
        st.warning(
            f"日報のファイル（ファイル名に「日報」を含むExcel）が見つかりません: {location}\n"
            "日報データ（Excel）をアップロードしてください。"
        )
        uploaded = st.file_uploader("日報データ（.xlsx）", type=["xlsx"])
        if uploaded is None:
            st.stop()
        tmp_path = Path(st.session_state.setdefault("_tmp_dir", ".streamlit_uploads"))
        tmp_path.mkdir(exist_ok=True)
        saved = tmp_path / uploaded.name
        saved.write_bytes(uploaded.getbuffer())
        df = _load_data(str(saved), saved.stat().st_mtime)

    if selfcare_path is not None:
        selfcare_df = _load_selfcare(str(selfcare_path), selfcare_path.stat().st_mtime)
        df = attach_condition(df, selfcare_df)
    else:
        st.info(
            "セルフケアシート（ファイル名に「セルフケア」を含むExcel）が見つかりません: "
            f"{user_dir or DATA_DIR}\n"
            f"体調マーカーはすべて「{_CONDITION_DEFAULT_STYLE['label']}」として表示されます。"
        )
        df["condition_points"] = None
        df["condition_label"] = None
    return _attach_weather_to(df)


def _condition_hover_text(label, points) -> str:
    if label is None:
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
    df, axis_label: str, axis_col: str, borders: list[BadBorder] | None = None
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
    # （直近1週間・直近1ヶ月など）も月/日形式にする。
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


def render_condition_legend() -> None:
    order = ("良好", "普通", "注意", "警戒", "異常")
    # 「× 注意」「× 警戒」「× 異常」「× 記録なし」は記号だけでは見分けにくいため、
    # マーカーと同じ色を凡例のテキストにも付けて区別できるようにする。
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
        + f"（0pt=良好、1〜{CONDITION_CAUTION_POINTS - 1}pt=普通、"
        + f"{CONDITION_CAUTION_POINTS}pt=注意、{CONDITION_WARNING_POINTS}pt=警戒、"
        + f"{CONDITION_ABNORMAL_THRESHOLD}pt以上=異常。"
        + "セルフケアシートに記録がない日は早退等の可能性を示す黒い×で表示）",
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


def main() -> None:
    st.set_page_config(page_title="体調・睡眠分析ダッシュボード", layout="wide")
    st.title("体調・睡眠分析ダッシュボード")
    st.caption(
        "利用者の日報・セルフケアのデータはローカルPC内で処理し、外部には送信しません。"
        "気象データを設定した場合のみ、施設の位置（緯度経度）と日付範囲を気象API"
        "（Open-Meteo）に送信して気圧・天気を取得します。"
    )

    user_dirs = list_user_dirs(DATA_DIR)
    with st.sidebar:
        st.header("表示設定")
        user_dir = (
            st.selectbox("利用者", user_dirs, format_func=lambda p: p.name) if user_dirs else None
        )

    df_all, weather_notice = load_source_dataframe(user_dir)
    # 気象データが無い場合は、気象の軸を選択肢・相関表から外す。
    axes = [(label, col) for label, col in AXIS_OPTIONS if df_all[col].notna().any()]

    with st.sidebar:
        axis_label = st.radio("縦軸（表示する項目）", [label for label, _ in axes])
        axis_col = dict(axes)[axis_label]
        period_label = st.radio("期間", PERIOD_OPTIONS, index=1)
        display_mode = st.radio("表示", DISPLAY_MODE_OPTIONS)
        if weather_notice:
            st.caption(weather_notice)

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

    fig = build_figure(df, axis_label, axis_col, borders)
    st.plotly_chart(fig, use_container_width=True)
    render_condition_legend()
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
