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

from health_dashboard.data_loader import (
    AXIS_OPTIONS,
    CONDITION_ABNORMAL_THRESHOLD,
    CONDITION_CAUTION_POINTS,
    CONDITION_WARNING_POINTS,
    attach_condition,
    filter_by_period,
    load_daily_reports,
    load_selfcare_points,
)
from health_dashboard.formatting import format_clock
from health_dashboard.rule_based_insight import generate_rule_based_insight
from health_dashboard.stats import summarize

DEFAULT_DATA_PATH = Path(__file__).parent / "data" / "日報データ.xlsx"
DEFAULT_SELFCARE_PATH = Path(__file__).parent / "data" / "オリジナルセルフケアシート .xlsx"
PERIOD_OPTIONS = ["直近1週間", "直近1ヶ月", "全期間"]

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
def _load_selfcare(path_str: str, mtime: float):
    return load_selfcare_points(path_str)


def load_source_dataframe():
    if DEFAULT_DATA_PATH.exists():
        df = _load_data(str(DEFAULT_DATA_PATH), DEFAULT_DATA_PATH.stat().st_mtime)
    else:
        st.warning(
            f"既定のデータファイルが見つかりません: {DEFAULT_DATA_PATH}\n"
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

    if DEFAULT_SELFCARE_PATH.exists():
        selfcare_df = _load_selfcare(
            str(DEFAULT_SELFCARE_PATH), DEFAULT_SELFCARE_PATH.stat().st_mtime
        )
        df = attach_condition(df, selfcare_df)
    else:
        st.info(
            f"セルフケアシートが見つかりません: {DEFAULT_SELFCARE_PATH}\n"
            f"体調マーカーはすべて「{_CONDITION_DEFAULT_STYLE['label']}」として表示されます。"
        )
        df["condition_points"] = None
        df["condition_label"] = None
    return df


def _condition_hover_text(label, points) -> str:
    if label is None:
        return _CONDITION_DEFAULT_STYLE["label"]
    if points is None or pd.isna(points):
        return label
    return f"{label}（{int(points)}pt）"


def build_figure(df, axis_label: str, axis_col: str) -> go.Figure:
    styles = [
        _CONDITION_STYLE.get(label, _CONDITION_DEFAULT_STYLE) for label in df["condition_label"]
    ]
    hover_condition = [
        _condition_hover_text(label, points)
        for label, points in zip(df["condition_label"], df["condition_points"], strict=False)
    ]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df[axis_col],
            mode="lines+markers",
            line=dict(color="#90a4ae"),
            marker=dict(
                size=11,
                symbol=[s["symbol"] for s in styles],
                color=[s["color"] for s in styles],
                line=dict(width=1, color="white"),
            ),
            customdata=hover_condition,
            hovertemplate="%{x|%-m/%-d}<br>値: %{y}<br>体調: %{customdata}<extra></extra>",
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
    elif axis_col == "quality_score":
        fig.update_yaxes(
            tickmode="array",
            tickvals=list(_QUALITY_SCORE_TICKS.keys()),
            ticktext=list(_QUALITY_SCORE_TICKS.values()),
        )

    _apply_date_axis_ticks(fig, df["date"].tolist())
    return fig


def render_condition_legend() -> None:
    order = ("良好", "普通", "注意", "警戒", "異常")
    st.caption(
        "マーカー（体調・セルフケアシート由来）: "
        + "　".join(_CONDITION_STYLE[k]["label"] for k in order)
        + f"　{_CONDITION_DEFAULT_STYLE['label']}"
        + f"（0pt=良好、1〜{CONDITION_CAUTION_POINTS - 1}pt=普通、"
        + f"{CONDITION_CAUTION_POINTS}pt=注意、{CONDITION_WARNING_POINTS}pt=警戒、"
        + f"{CONDITION_ABNORMAL_THRESHOLD}pt以上=異常。"
        + "セルフケアシートに記録がない日は早退等の可能性を示す黒い×で表示）"
    )


def render_stats(df, axis_col: str) -> None:
    s = summarize(df[axis_col])
    cols = st.columns(5)
    cols[0].metric("データ件数", s.count)
    cols[1].metric("平均", f"{s.mean:.2f}" if s.mean is not None else "―")
    cols[2].metric("中央値", f"{s.median:.2f}" if s.median is not None else "―")
    cols[3].metric("最頻値", f"{s.mode:.2f}" if s.mode is not None else "―")
    cols[4].metric("ばらつき（標準偏差）", f"{s.std:.2f}" if s.std is not None else "―")


def render_insight(df, axis_col: str, axis_label: str, period_label: str) -> None:
    st.subheader("振り返りコメント")
    st.caption(
        "※ 医学的な診断は行いません。あくまで傾向の提示と、面談での対話のきっかけを"
        "提案するための参考コメントです。"
    )

    st.info(generate_rule_based_insight(df, axis_col, axis_label, period_label))


def main() -> None:
    st.set_page_config(page_title="体調・睡眠分析ダッシュボード", layout="wide")
    st.title("体調・睡眠分析ダッシュボード")
    st.caption("すべてのデータ処理はローカルPC内で完結し、外部には一切送信されません。")

    df_all = load_source_dataframe()

    with st.sidebar:
        st.header("表示設定")
        axis_label = st.radio("縦軸（表示する項目）", [label for label, _ in AXIS_OPTIONS])
        axis_col = dict(AXIS_OPTIONS)[axis_label]
        period_label = st.radio("期間", PERIOD_OPTIONS, index=1)

    df = filter_by_period(df_all, period_label)

    if df.empty:
        st.warning("選択した期間にデータがありません。")
        return

    fig = build_figure(df, axis_label, axis_col)
    st.plotly_chart(fig, use_container_width=True)
    render_condition_legend()

    render_stats(df, axis_col)
    st.divider()
    render_insight(df, axis_col, axis_label, period_label)


if __name__ == "__main__":
    main()
