import datetime as dt

import openpyxl
import pandas as pd
import pytest

from health_dashboard.data_loader import (
    CONDITION_ABNORMAL_THRESHOLD,
    CONDITION_CAUTION_POINTS,
    CONDITION_WARNING_POINTS,
    _coerce_time,
    attach_condition,
    classify_absence_reason,
    condition_label_from_points,
    filter_by_period,
    find_absence_file,
    find_user_files,
    list_user_dirs,
    load_absence_dates,
    load_absence_records,
    load_daily_reports,
    load_selfcare_points,
    normalize_bedtime,
    parse_mood_types,
    parse_sleep_quality,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (dt.time(9, 30), dt.time(21, 30)),
        (dt.time(11, 0), dt.time(23, 0)),
        (dt.time(0, 30), dt.time(0, 30)),
        (dt.time(23, 0), dt.time(23, 0)),
        (None, None),
    ],
)
def test_normalize_bedtime(raw, expected):
    assert normalize_bedtime(raw) == expected


@pytest.mark.parametrize(
    ("text", "expected_label", "expected_awakenings"),
    [
        ("悪い, 中途覚醒３以上", "悪い", 3),
        ("良好", "良好", 0),
        ("普通, 中途覚醒２, 早朝覚醒", "普通", 3),
        ("普通, 悪い, 中途覚醒３以上", "悪い", 3),
        ("良好, 早朝覚醒", "良好", 1),
        (None, None, 0),
    ],
)
def test_parse_sleep_quality(text, expected_label, expected_awakenings):
    label, awakenings = parse_sleep_quality(text)
    assert label == expected_label
    assert awakenings == expected_awakenings


def _write_sample_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "シート1"
    # 実データに合わせ、先頭2行は空、3行目にヘッダーを置く。
    ws.append([None] * 15)
    ws.append([None] * 15)
    ws.append(
        [
            None,
            "日付",
            "曜日",
            "通所時間",
            "目標",
            "就寝時間",
            "起床時間",
            "睡眠時間",
            "睡眠の質",
            "気分（起床時）",
            "気分（通所時）",
            "気分の種類",
        ]
    )
    ws.append(
        [
            None,
            dt.datetime(2026, 5, 28),
            "木",
            dt.time(9, 57),
            "目標A",
            dt.time(9, 30),
            dt.time(6, 50),
            "8時間",
            "悪い, 中途覚醒３以上",
            3.0,
            8.0,
            "落ち着いている",
        ]
    )
    ws.append(
        [
            None,
            dt.datetime(2026, 6, 4),
            "木",
            dt.time(10, 0),
            "目標B",
            dt.time(9, 30),
            dt.time(6, 20),
            "8時間",
            "良好",
            7.0,
            6.0,
            "憂鬱",
        ]
    )
    ws.append(
        [
            None,
            dt.datetime(2026, 6, 5),
            "金",
            dt.time(9, 0),
            "目標C",
            dt.time(0, 30),  # 日付をまたいだ後の就寝（正規化されずそのまま0:30扱い）
            dt.time(7, 0),
            "7時間",
            "良好",
            6.0,
            7.0,
            "落ち着いている, 心配（仕事のこと）",  # 複数選択。選択肢にない記入は「その他」
        ]
    )
    wb.save(path)


def test_load_daily_reports(tmp_path):
    path = tmp_path / "sample.xlsx"
    _write_sample_workbook(path)

    df = load_daily_reports(path)

    assert len(df) == 3
    assert list(df["date"]) == [dt.date(2026, 5, 28), dt.date(2026, 6, 4), dt.date(2026, 6, 5)]
    # 就寝9:30 -> 21:30として解釈される（12時台以降なのでそのままの数値軸）
    assert df.loc[0, "bedtime_hours"] == pytest.approx(21.5)
    assert df.loc[0, "quality_label"] == "悪い"
    assert df.loc[0, "night_awakenings"] == 3
    assert df.loc[1, "quality_label"] == "良好"
    assert df.loc[1, "night_awakenings"] == 0
    assert df.loc[0, "mood_wake"] == 3.0
    assert df.loc[0, "mood_commute"] == 8.0
    # 就寝21:30(21.5) -> 起床6:50(6.83..) は9時間20分（9.33...時間）
    assert df.loc[0, "sleep_duration_hours"] == pytest.approx(9 + 20 / 60)
    # 就寝0:30(日付またぎ後、24.5) -> 起床7:00(7.0) は6時間30分
    assert df.loc[2, "sleep_duration_hours"] == pytest.approx(6.5)
    assert list(df["mood_type"]) == [["落ち着いている"], ["憂鬱"], ["落ち着いている", "その他"]]


def _write_selfcare_workbook(path, sheet_specs):
    """sheet_specs: list of (sheet_title, [(date, weekday, mark_a, mark_b, mark_c, memo), ...])"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheet_specs:
        ws = wb.create_sheet(title)
        ws.append([None, None, "グループ", "グループ", "グループ", "備考"])
        ws.append(["チェック項目", None, "A", "B", "C", None])
        ws.append([None] * 6)
        ws.append(["日付", "曜日", None, None, None, None])
        for row in rows:
            ws.append(list(row))
    wb.save(path)


def test_load_selfcare_points_skips_header_and_blank_rows(tmp_path):
    path = tmp_path / "selfcare.xlsx"
    _write_selfcare_workbook(
        path,
        [
            (
                "202601",
                [
                    (dt.datetime(2026, 1, 1), "木", "〇", "△", "✕", "メモ1"),  # 0+1+2=3
                    (dt.datetime(2026, 1, 2), "金", "〇", "〇", "〇", None),  # 0
                    (dt.datetime(2026, 1, 3), "土", None, None, None, None),  # 記録なし -> 除外
                ],
            )
        ],
    )

    df = load_selfcare_points(path)

    assert list(df["date"]) == [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]
    assert list(df["condition_points"]) == [3, 0]


def test_load_selfcare_points_first_data_row_included(tmp_path):
    """月初(シート5行目=最初のデータ行)が読み飛ばされないことを確認する回帰テスト。"""
    path = tmp_path / "selfcare.xlsx"
    _write_selfcare_workbook(
        path,
        [("202602", [(dt.datetime(2026, 2, 1), "日", "✕", "✕", "✕", None)])],
    )

    df = load_selfcare_points(path)

    assert list(df["date"]) == [dt.date(2026, 2, 1)]
    assert list(df["condition_points"]) == [6]


def test_load_selfcare_points_duplicate_date_keeps_first_sheet(tmp_path):
    path = tmp_path / "selfcare.xlsx"
    _write_selfcare_workbook(
        path,
        [
            ("202601", [(dt.datetime(2026, 1, 1), "木", "〇", "〇", "〇", None)]),  # 0
            ("202601b", [(dt.datetime(2026, 1, 1), "木", "✕", "✕", "✕", None)]),  # 6
        ],
    )

    df = load_selfcare_points(path)

    assert list(df["condition_points"]) == [0]


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (None, None),
        (float("nan"), None),
        (0, "良好"),
        (1, "普通"),
        (CONDITION_CAUTION_POINTS - 1, "普通"),
        (CONDITION_CAUTION_POINTS, "注意"),
        (CONDITION_WARNING_POINTS, "警戒"),
        (CONDITION_ABNORMAL_THRESHOLD, "異常"),
        (CONDITION_ABNORMAL_THRESHOLD + 10, "異常"),
    ],
)
def test_condition_label_from_points(points, expected):
    assert condition_label_from_points(points) == expected


def test_attach_condition():
    df = pd.DataFrame({"date": [dt.date(2026, 1, 1), dt.date(2026, 1, 2)], "value": [1, 2]})
    selfcare_df = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "condition_points": [7]})

    merged = attach_condition(df, selfcare_df)

    assert merged.loc[0, "condition_points"] == 7
    assert merged.loc[0, "condition_label"] == "異常"
    assert pd.isna(merged.loc[1, "condition_points"])
    assert merged.loc[1, "condition_label"] is None


def test_filter_by_period():
    import pandas as pd

    df = pd.DataFrame(
        {
            "date": [dt.date(2026, 1, 1), dt.date(2026, 1, 15), dt.date(2026, 1, 31)],
            "value": [1, 2, 3],
        }
    )
    # 最新日(1/31)から30日間（1/2〜1/31）。1/1は含まれず、1/15は含まれる。
    result = filter_by_period(df, "直近1ヶ月")
    assert list(result["value"]) == [2, 3]

    result = filter_by_period(df, "全期間")
    assert list(result["value"]) == [1, 2, 3]


def test_filter_by_period_rejects_removed_one_week_option():
    df = pd.DataFrame({"date": [dt.date(2026, 1, 1)], "value": [1]})

    with pytest.raises(ValueError):
        filter_by_period(df, "直近1週間")


def test_list_user_dirs_returns_sorted_subfolders_only(tmp_path):
    (tmp_path / "b利用者").mkdir()
    (tmp_path / "a利用者").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "memo.txt").write_text("x")

    assert [p.name for p in list_user_dirs(tmp_path)] == ["a利用者", "b利用者"]


def test_list_user_dirs_missing_dir_returns_empty(tmp_path):
    assert list_user_dirs(tmp_path / "none") == []


def test_find_user_files_matches_by_keyword_in_name(tmp_path):
    (tmp_path / "西村_日報（2026年9月）.xlsx").write_text("")
    (tmp_path / "オリジナルセルフケアシート .xlsx").write_text("")
    (tmp_path / "その他.xlsx").write_text("")
    (tmp_path / "~$日報.xlsx").write_text("")  # Excelの一時ファイルは無視

    daily, selfcare = find_user_files(tmp_path)

    assert daily.name == "西村_日報（2026年9月）.xlsx"
    assert selfcare.name == "オリジナルセルフケアシート .xlsx"


def test_find_user_files_missing_returns_none(tmp_path):
    (tmp_path / "日報.xlsx").write_text("")

    daily, selfcare = find_user_files(tmp_path)

    assert daily is not None
    assert selfcare is None


def test_find_user_files_prefers_newest_when_multiple(tmp_path):
    import os

    old = tmp_path / "日報_旧.xlsx"
    new = tmp_path / "日報_新.xlsx"
    old.write_text("")
    new.write_text("")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    daily, _ = find_user_files(tmp_path)

    assert daily.name == "日報_新.xlsx"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("9:30:00 午前", dt.time(9, 30)),
        ("6:50:00 午前", dt.time(6, 50)),
        ("9:30:00 午後", dt.time(21, 30)),
        ("12:10:00 午前", dt.time(0, 10)),
        ("12:10:00 午後", dt.time(12, 10)),
        ("9:30 PM", dt.time(21, 30)),
        ("06:50:00", dt.time(6, 50)),
        ("6:50", dt.time(6, 50)),
        ("なし", None),
        ("", None),
        (None, None),
    ],
)
def test_coerce_time_text_formats(raw, expected):
    assert _coerce_time(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("うれしい", ["うれしい"]),
        (" 落ち着いている ", ["落ち着いている"]),
        ("落ち着いている, 憂鬱", ["落ち着いている", "憂鬱"]),
        ("不安、緊張,心配", ["不安", "緊張", "心配"]),
        ("不安, 不安", ["不安"]),
        ("その他", ["その他"]),
        ("眠い", ["その他"]),
        ("不安, 眠い, だるい", ["不安", "その他"]),
        ("", None),
        ("   ", None),
        (" , ", None),
        (None, None),
        (float("nan"), None),
    ],
)
def test_parse_mood_types(raw, expected):
    assert parse_mood_types(raw) == expected


def test_load_daily_reports_without_mood_type_column(tmp_path):
    path = tmp_path / "old_format.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["日付", "就寝時間", "起床時間", "睡眠の質", "気分（起床時）", "気分（通所時）"])
    ws.append([dt.datetime(2026, 6, 4), dt.time(9, 30), dt.time(6, 20), "良好", 7.0, 6.0])
    wb.save(path)

    df = load_daily_reports(path)

    assert df["mood_type"].isna().all()


def test_find_absence_file_by_keyword(tmp_path):
    (tmp_path / "日報.xlsx").write_text("")
    (tmp_path / "欠席（回答）.xlsx").write_text("")

    assert find_absence_file(tmp_path).name == "欠席（回答）.xlsx"


def test_find_absence_file_missing_returns_none(tmp_path):
    (tmp_path / "日報.xlsx").write_text("")

    assert find_absence_file(tmp_path) is None


def test_load_absence_dates_uses_timestamp_date_and_dedups(tmp_path):
    path = tmp_path / "欠席.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["タイムスタンプ", "欠席理由"])
    ws.append([dt.datetime(2026, 6, 18, 10, 53, 41), "通院"])
    ws.append([dt.datetime(2026, 6, 18, 18, 0, 0), "通院"])  # 同じ日の再提出
    ws.append([dt.datetime(2026, 6, 24, 12, 0, 8), "体調不良"])
    wb.save(path)

    assert load_absence_dates(path) == [dt.date(2026, 6, 18), dt.date(2026, 6, 24)]


def test_load_absence_dates_without_timestamp_column_raises(tmp_path):
    path = tmp_path / "欠席.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["日付", "欠席理由"])
    wb.save(path)

    with pytest.raises(ValueError, match="タイムスタンプ"):
        load_absence_dates(path)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("精神不調", "mental"),
        ("体調不良", "physical"),
        ("通院", "other"),
        ("家庭の都合", "other"),
        ("精神不調, 体調不良", "mental"),  # 両方なら精神的な理由を優先
        ("", "other"),
        (None, "other"),
        (float("nan"), "other"),
    ],
)
def test_classify_absence_reason(reason, expected):
    assert classify_absence_reason(reason) == expected


def _write_absence_workbook(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["タイムスタンプ", "欠席理由"])
    for row in rows:
        ws.append(list(row))
    wb.save(path)


def test_load_absence_records_returns_date_and_category_sorted(tmp_path):
    path = tmp_path / "欠席.xlsx"
    _write_absence_workbook(
        path,
        [
            (dt.datetime(2026, 6, 24, 12, 0), "体調不良"),
            (dt.datetime(2026, 6, 18, 10, 0), "精神不調"),
            (dt.datetime(2026, 7, 1, 9, 0), "通院"),
        ],
    )

    assert load_absence_records(path) == [
        (dt.date(2026, 6, 18), "mental"),
        (dt.date(2026, 6, 24), "physical"),
        (dt.date(2026, 7, 1), "other"),
    ]


def test_load_absence_records_same_day_keeps_highest_priority(tmp_path):
    path = tmp_path / "欠席.xlsx"
    _write_absence_workbook(
        path,
        [
            (dt.datetime(2026, 6, 18, 9, 0), "体調不良"),
            (dt.datetime(2026, 6, 18, 18, 0), "精神不調"),  # 同じ日の再提出
            (dt.datetime(2026, 6, 18, 19, 0), "通院"),
        ],
    )

    assert load_absence_records(path) == [(dt.date(2026, 6, 18), "mental")]


def test_load_absence_records_without_reason_column_is_other(tmp_path):
    path = tmp_path / "欠席.xlsx"
    wb = openpyxl.Workbook()
    wb.active.append(["タイムスタンプ"])
    wb.active.append([dt.datetime(2026, 6, 18, 9, 0)])
    wb.save(path)

    assert load_absence_records(path) == [(dt.date(2026, 6, 18), "other")]
