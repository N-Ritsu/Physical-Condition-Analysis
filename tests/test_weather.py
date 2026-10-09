import datetime as dt
import io
import json
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest

from health_dashboard.weather import (
    attach_weather,
    classify_weather,
    fetch_weather,
    load_weather_location,
    parse_coordinate,
    parse_weather_response,
    save_weather_location,
)


@pytest.mark.parametrize(
    ("precipitation", "snowfall", "cloud", "expected"),
    [
        (5.0, 0.0, 90.0, "雨"),
        (1.0, 0.0, 10.0, "雨"),  # 1mmちょうどは雨
        (0.9, 0.0, 90.0, "曇"),  # 弱い降水だけなら雨にしない
        (0.0, 0.0, 59.9, "晴"),
        (0.0, 0.0, 60.0, "曇"),
        (3.0, 2.0, 100.0, "雪"),
        (0.2, 0.5, 100.0, "曇"),  # 降雪量が少なければ雪にしない
        (None, 0.0, 50.0, None),
        (0.0, 0.0, None, None),
        (float("nan"), 0.0, 50.0, None),
    ],
)
def test_classify_weather(precipitation, snowfall, cloud, expected):
    assert classify_weather(precipitation, snowfall, cloud) == expected


def _payload():
    # 2日分（9/1, 9/2）。9/1は1000hPa・雲量100%・降水5mm（雨）、
    # 9/2は1010hPa（欠測1時間を含む）・雲量0%・降水なし（晴）。
    times = [f"2026-09-01T{h:02d}:00" for h in range(24)] + [
        f"2026-09-02T{h:02d}:00" for h in range(24)
    ]
    return {
        "hourly": {
            "time": times,
            "pressure_msl": [1000.0] * 24 + [1010.0] * 23 + [None],
            "cloud_cover": [100.0] * 24 + [0.0] * 24,
        },
        "daily": {
            "time": ["2026-09-01", "2026-09-02"],
            "precipitation_sum": [5.0, 0.0],
            "snowfall_sum": [0.0, 0.0],
        },
    }


def test_parse_weather_response_daily_mean_change_and_label():
    result = parse_weather_response(_payload())

    assert list(result["date"]) == [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]
    assert list(result["pressure_hpa"]) == [1000.0, 1010.0]
    assert pd.isna(result.loc[0, "pressure_change_hpa"])
    assert result.loc[1, "pressure_change_hpa"] == pytest.approx(10.0)
    assert list(result["weather_label"]) == ["雨", "晴"]


def test_parse_weather_response_missing_precipitation_gives_no_label():
    payload = _payload()
    payload["daily"]["precipitation_sum"] = [None, 0.0]

    result = parse_weather_response(payload)

    assert pd.isna(result.loc[0, "weather_label"])  # pandas 2ではNone、3ではNaN
    assert result.loc[1, "weather_label"] == "晴"


def test_attach_weather_merges_by_date():
    df = pd.DataFrame({"date": [dt.date(2026, 9, 2), dt.date(2026, 9, 5)], "v": [1, 2]})
    weather = parse_weather_response(_payload())

    merged = attach_weather(df, weather)

    assert merged.loc[0, "pressure_hpa"] == 1010.0
    assert merged.loc[0, "weather_label"] == "晴"
    assert merged.loc[0, "weather_score"] == 2  # 雨=0・曇=1・晴=2
    assert pd.isna(merged.loc[1, "pressure_hpa"])  # 気象データが無い日は空
    assert len(merged) == 2


def test_attach_weather_none_adds_empty_columns():
    df = pd.DataFrame({"date": [dt.date(2026, 9, 2)], "v": [1]})

    result = attach_weather(df, None)

    assert result["pressure_hpa"].isna().all()
    assert result["pressure_change_hpa"].isna().all()
    assert result["weather_label"].isna().all()
    assert result["weather_score"].isna().all()
    assert result["pressure_hpa"].dtype == float


def test_load_weather_location_valid(tmp_path):
    path = tmp_path / "weather_config.json"
    path.write_text(json.dumps({"latitude": 35.68, "longitude": 139.69}), encoding="utf-8")

    assert load_weather_location(path) == (35.68, 139.69)


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"latitude": None, "longitude": None}),  # 初期状態（未設定）
        json.dumps({"latitude": 35.0}),
        json.dumps({"latitude": "abc", "longitude": 1}),
        json.dumps({"latitude": 91, "longitude": 0}),
        json.dumps({"latitude": 0, "longitude": 181}),
    ],
)
def test_load_weather_location_invalid_returns_none(tmp_path, content):
    path = tmp_path / "weather_config.json"
    path.write_text(content, encoding="utf-8")

    assert load_weather_location(path) is None


def test_load_weather_location_missing_file_returns_none(tmp_path):
    assert load_weather_location(tmp_path / "none.json") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("35.6812", 35.6812),
        (" 35.68 ", 35.68),
        ("３５．６８", 35.68),  # 全角
        ("35.68°", 35.68),
        ("-12", -12.0),
    ],
)
def test_parse_coordinate_accepts_common_inputs(text, expected):
    assert parse_coordinate(text, "緯度", -90, 90) == (expected, None)


@pytest.mark.parametrize("text", ["", "  ", "abc", "35,68", "nan", "inf", "91", "-90.1"])
def test_parse_coordinate_rejects_bad_inputs(text):
    value, error = parse_coordinate(text, "緯度", -90, 90)

    assert value is None
    assert "緯度" in error


def test_save_weather_location_roundtrip(tmp_path):
    path = tmp_path / "home" / "weather_config.json"

    save_weather_location(path, 35.6812, 139.7671)

    assert load_weather_location(path) == (35.6812, 139.7671)
    # 一時ファイルが残らない
    assert [p.name for p in path.parent.iterdir()] == ["weather_config.json"]


def test_save_weather_location_none_clears_setting(tmp_path):
    path = tmp_path / "weather_config.json"
    save_weather_location(path, 35.0, 139.0)

    save_weather_location(path, None, None)

    assert load_weather_location(path) is None
    assert json.loads(path.read_text(encoding="utf-8")) == {"latitude": None, "longitude": None}


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_fetch_weather_sends_only_rounded_location_and_dates():
    captured = {}

    def fake_urlopen(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return _FakeResponse(json.dumps(_payload()).encode("utf-8"))

    result = fetch_weather(
        35.689487, 139.691706, dt.date(2026, 9, 2), dt.date(2026, 9, 2), urlopen=fake_urlopen
    )

    parsed = urlparse(captured["url"])
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.scheme == "https"
    assert query["latitude"] == "35.69"
    assert query["longitude"] == "139.69"
    assert query["start_date"] == "2026-09-01"  # 前日差の算出のため前日から取得する
    assert query["end_date"] == "2026-09-02"
    # 位置・日付・取得項目以外（個人を特定しうる情報）は送らない。
    assert set(query) == {
        "latitude",
        "longitude",
        "start_date",
        "end_date",
        "hourly",
        "daily",
        "timezone",
    }
    assert captured["timeout"] > 0
    assert len(result) == 2


def test_attach_weather_score_mapping_and_snow_excluded():
    df = pd.DataFrame({"date": [dt.date(2026, 1, d) for d in range(1, 5)]})
    weather = pd.DataFrame(
        {
            "date": df["date"],
            "pressure_hpa": [1000.0] * 4,
            "pressure_change_hpa": [0.0] * 4,
            "weather_label": ["雨", "曇", "晴", "雪"],
        }
    )

    merged = attach_weather(df, weather)

    assert merged["weather_score"].iloc[:3].tolist() == [0, 1, 2]
    assert pd.isna(merged["weather_score"].iloc[3])
