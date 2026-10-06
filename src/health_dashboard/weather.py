"""気象データ（気圧・天気）の取得と日報データへの結合。

気象APIに送る情報は「施設の位置（緯度経度）」と「日付範囲」だけで、利用者を特定する
情報・日報・セルフケアの内容は一切含まれない。位置情報は精度を下げて（小数点以下2桁）
送信し、設定ファイルが無い場合は通信自体を行わない。

取得値は気象庁の観測所の実測値ではなく、広域の再解析データ（格子単位）に基づく
参考値である。特に天気（晴/曇/雨/雪）は降水量・雲量から機械的に判定した目安で、
実際の天気と食い違う日がありうる。
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pandas as pd

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
REQUEST_TIMEOUT_SECONDS = 10
# 位置情報の送信精度（小数点以下の桁数）。2桁は約1km四方で、気圧・天気の取得には十分。
_COORDINATE_DECIMALS = 2

# 天気の判定基準（classify_weather参照）。
_RAIN_MIN_MM = 1.0  # 日降水量がこれ以上なら雨
_SNOW_MIN_CM = 1.0  # 日降雪量がこれ以上なら雪
_CLOUDY_MIN_PERCENT = 60.0  # 雨・雪でない日で、平均雲量がこれ以上なら曇、未満なら晴

WEATHER_COLUMNS = ["pressure_hpa", "pressure_change_hpa", "weather_label"]
WEATHER_ORDER = ["晴", "曇", "雨", "雪"]


def classify_weather(
    precipitation_mm: float | None, snowfall_cm: float | None, cloud_cover_pct: float | None
) -> str | None:
    """1日の降水量・降雪量・平均雲量から 晴/曇/雨/雪 を判定する。

    APIの天気コードは「その日で最も深刻なもの」を返すため、ごく弱い霧雨でも雨になり
    実感とずれる。そこで日降水量が気象庁の雨日の目安（1mm）以上かどうかで判定する。
    """
    if precipitation_mm is None or pd.isna(precipitation_mm):
        return None
    if snowfall_cm is not None and not pd.isna(snowfall_cm) and snowfall_cm >= _SNOW_MIN_CM:
        return "雪"
    if precipitation_mm >= _RAIN_MIN_MM:
        return "雨"
    if cloud_cover_pct is None or pd.isna(cloud_cover_pct):
        return None
    return "晴" if cloud_cover_pct < _CLOUDY_MIN_PERCENT else "曇"


def load_weather_location(config_path: str | Path) -> tuple[float, float] | None:
    """設定ファイルから (緯度, 経度) を読む。ファイルが無い・不正な場合はNone。"""
    path = Path(config_path)
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        latitude = float(config["latitude"])
        longitude = float(config["longitude"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def parse_weather_response(payload: dict) -> pd.DataFrame:
    """気象APIのレスポンスを、日付ごとの気圧（日平均）・前日差・天気に整形する。"""
    hourly = pd.DataFrame(
        {
            "date": [dt.date.fromisoformat(t[:10]) for t in payload["hourly"]["time"]],
            "pressure": pd.Series(payload["hourly"]["pressure_msl"], dtype=float),
            "cloud_cover": pd.Series(payload["hourly"]["cloud_cover"], dtype=float),
        }
    )
    hourly_mean = hourly.groupby("date", as_index=False)[["pressure", "cloud_cover"]].mean()
    hourly_mean["pressure_hpa"] = hourly_mean["pressure"].round(1)

    daily_raw = payload["daily"]
    daily = pd.DataFrame(
        {
            "date": [dt.date.fromisoformat(t) for t in daily_raw["time"]],
            "precipitation": pd.Series(daily_raw["precipitation_sum"], dtype=float),
            "snowfall": pd.Series(daily_raw["snowfall_sum"], dtype=float),
        }
    )

    result = hourly_mean.merge(daily, on="date", how="outer")
    result = result.sort_values("date").reset_index(drop=True)
    result["weather_label"] = [
        classify_weather(precipitation, snowfall, cloud)
        for precipitation, snowfall, cloud in zip(
            result["precipitation"], result["snowfall"], result["cloud_cover"], strict=True
        )
    ]
    result["pressure_change_hpa"] = result["pressure_hpa"].diff().round(1)
    return result[["date", *WEATHER_COLUMNS]]


def fetch_weather(
    latitude: float,
    longitude: float,
    start: dt.date,
    end: dt.date,
    urlopen: Callable = urllib.request.urlopen,
) -> pd.DataFrame:
    """start〜endの気圧・天気を取得する。前日差を出すため、startの前日分も取得する。

    ネットワークエラー等は例外のまま呼び出し側に伝える。
    """
    query = urllib.parse.urlencode(
        {
            "latitude": round(latitude, _COORDINATE_DECIMALS),
            "longitude": round(longitude, _COORDINATE_DECIMALS),
            "start_date": (start - dt.timedelta(days=1)).isoformat(),
            "end_date": end.isoformat(),
            "hourly": "pressure_msl,cloud_cover",
            "daily": "precipitation_sum,snowfall_sum",
            "timezone": "Asia/Tokyo",
        }
    )
    with urlopen(f"{ARCHIVE_URL}?{query}", timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.load(response)
    return parse_weather_response(payload)


def attach_weather(df: pd.DataFrame, weather_df: pd.DataFrame | None) -> pd.DataFrame:
    """日報データに気象列を日付で結合する。weather_dfがNoneなら気象列は空（NaN）になる。"""
    if weather_df is None:
        result = df.copy()
        result["pressure_hpa"] = float("nan")
        result["pressure_change_hpa"] = float("nan")
        result["weather_label"] = None
        return result
    return df.merge(weather_df[["date", *WEATHER_COLUMNS]], on="date", how="left")
