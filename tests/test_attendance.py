import datetime as dt

import pytest

from health_dashboard.attendance import weekly_attendance

MON = dt.date(2026, 6, 1)  # 2026-06-01 は月曜


def _day(offset: int) -> dt.date:
    return MON + dt.timedelta(days=offset)


def test_weekly_counts_and_rate():
    attended = [_day(0), _day(1), _day(2), _day(3)]  # 月〜木
    absent = [_day(4)]  # 金

    weekly = weekly_attendance(attended, absent)

    assert len(weekly) == 1
    row = weekly.iloc[0]
    assert row["week_start"] == MON
    assert row["week_end"] == _day(4)
    assert row["attended_days"] == 4
    assert row["absent_days"] == 1
    assert row["attendance_rate"] == pytest.approx(0.8)


def test_weekends_are_ignored():
    weekly = weekly_attendance([_day(0), _day(5), _day(6)], [_day(5)])  # 土日は対象外

    assert len(weekly) == 1
    assert weekly.iloc[0]["attended_days"] == 1
    assert weekly.iloc[0]["absent_days"] == 0
    assert weekly.iloc[0]["attendance_rate"] == 1.0


def test_same_day_in_both_counts_as_attended():
    weekly = weekly_attendance([_day(1)], [_day(1), _day(2)])

    row = weekly.iloc[0]
    assert row["attended_days"] == 1
    assert row["absent_days"] == 1  # 重複した日は出席のみ。欠席は水曜の1日
    assert row["attendance_rate"] == pytest.approx(0.5)


def test_week_with_only_absence_has_zero_rate():
    weekly = weekly_attendance([], [_day(0), _day(1)])

    assert weekly.iloc[0]["attended_days"] == 0
    assert weekly.iloc[0]["attendance_rate"] == 0.0


def test_weeks_are_split_by_monday_and_sorted():
    next_monday = _day(7)
    weekly = weekly_attendance([next_monday, _day(4)], [])  # 前週の金曜と翌週の月曜

    assert list(weekly["week_start"]) == [MON, next_monday]
    assert list(weekly["attended_days"]) == [1, 1]


def test_weeks_without_any_record_are_skipped():
    weekly = weekly_attendance([_day(0), _day(14)], [])  # 間の1週間は記録なし

    assert list(weekly["week_start"]) == [MON, _day(14)]


def test_empty_input_returns_empty_frame_with_columns():
    weekly = weekly_attendance([], [])

    assert weekly.empty
    assert "attendance_rate" in weekly.columns


def test_score_is_attended_days_times_rate():
    # 5日予定で1日欠席（4日出席・80%）→ 4 × 0.8 = 3.2
    one_absence = weekly_attendance([_day(0), _day(1), _day(2), _day(3)], [_day(4)])
    assert one_absence.iloc[0]["attendance_score"] == pytest.approx(3.2)

    # 4日出席・欠席なし → 4.0
    four_days = weekly_attendance([_day(0), _day(1), _day(2), _day(3)], [])
    assert four_days.iloc[0]["attendance_score"] == pytest.approx(4.0)


def test_week_with_absence_scores_lower_than_fewer_days_without_absence():
    full_week_with_absence = weekly_attendance([_day(0), _day(1), _day(2), _day(3)], [_day(4)])
    four_days_no_absence = weekly_attendance([_day(7), _day(8), _day(9), _day(10)], [])

    assert (
        full_week_with_absence.iloc[0]["attendance_score"]
        < four_days_no_absence.iloc[0]["attendance_score"]
    )


def test_all_present_week_scores_five_and_absent_week_scores_zero():
    present = weekly_attendance([_day(i) for i in range(5)], [])
    absent = weekly_attendance([], [_day(0)])

    assert present.iloc[0]["attendance_score"] == pytest.approx(5.0)
    assert absent.iloc[0]["attendance_score"] == 0.0


def test_unfinished_latest_week_is_excluded():
    this_week_wed = _day(7 + 2)  # 翌週の水曜（その週はまだ終わっていない）
    weekly = weekly_attendance([_day(0), _day(1), _day(7), _day(8)], [], as_of=this_week_wed)

    assert list(weekly["week_start"]) == [MON]  # 翌週（_day(7)〜）は含めない


def test_week_is_included_only_after_its_friday_has_passed():
    friday = _day(4)

    on_friday = weekly_attendance([_day(0)], [], as_of=friday)
    on_saturday = weekly_attendance([_day(0)], [], as_of=friday + dt.timedelta(days=1))

    assert on_friday.empty  # 金曜当日はまだ終わっていない
    assert len(on_saturday) == 1
