"""週ごと（月〜金）の出席日数・出席率の集計。

出席日数は日報のある日、欠席日数は欠席フォームの提出日から数える。
出席率は 出席日数 ÷（出席日数 + 欠席日数）で、日報も欠席連絡も無い日（休み・祝日等）は
通所予定日に含めない。

週ごとの指標「出席日数×出席率」（attendance_score）は、欠席した週が、欠席の無い週より
必ず低い値になる。例: 4日出席・欠席なし → 4.0、5日予定で1日欠席（4日出席・80%）→ 3.2。
まだ終わっていない週（金曜が今日以降の週）は、半端なデータなので集計から外す。
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Iterable

import pandas as pd

WEEKDAYS_PER_WEEK = 5


def _week_start(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def weekly_attendance(
    attended_dates: Iterable[dt.date],
    absent_dates: Iterable[dt.date],
    as_of: dt.date | None = None,
) -> pd.DataFrame:
    """月〜金の週ごとに、出席日数・欠席日数・出席率・出席日数×出席率を集計する。

    土日の記録は対象外。同じ日が出席と欠席の両方にある場合は出席として数える。
    記録が1日も無い週は含まない。金曜（週の終わり）が as_of（既定は今日）以降の、
    まだ終わっていない週も含まない。
    """
    as_of = as_of or dt.date.today()
    attended = {d for d in attended_dates if d.weekday() < WEEKDAYS_PER_WEEK}
    absent = {d for d in absent_dates if d.weekday() < WEEKDAYS_PER_WEEK} - attended

    attended_by_week = Counter(_week_start(d) for d in attended)
    absent_by_week = Counter(_week_start(d) for d in absent)

    rows = []
    for start in sorted(set(attended_by_week) | set(absent_by_week)):
        week_end = start + dt.timedelta(days=WEEKDAYS_PER_WEEK - 1)
        if week_end >= as_of:
            continue
        attended_days = attended_by_week[start]
        absent_days = absent_by_week[start]
        rate = attended_days / (attended_days + absent_days)
        rows.append(
            {
                "week_start": start,
                "week_end": week_end,
                "attended_days": attended_days,
                "absent_days": absent_days,
                "attendance_rate": rate,
                "attendance_score": attended_days * rate,
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "week_start",
            "week_end",
            "attended_days",
            "absent_days",
            "attendance_rate",
            "attendance_score",
        ],
    )
