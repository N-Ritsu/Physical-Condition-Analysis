"""データ・設定ファイルの置き場所。

通常（ソースから実行）はリポジトリ直下を使う。配布版では、起動プログラム
（launcher.py）が環境変数 HEALTH_DASHBOARD_HOME に利用者のドキュメント内のフォルダを
設定するため、アプリ本体を更新してもデータや設定が消えない。
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_ENV_VAR = "HEALTH_DASHBOARD_HOME"
# 配布版の起動プログラムから起動されたときに設定される。終了ボタンの表示判定に使う。
LAUNCHER_ENV_VAR = "HEALTH_DASHBOARD_LAUNCHER"

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def app_home() -> Path:
    """データ（data/）と設定（weather_config.json）、ログを含むフォルダ。"""
    override = os.environ.get(HOME_ENV_VAR)
    return Path(override) if override else _PROJECT_ROOT


def data_dir() -> Path:
    return app_home() / "data"


def weather_config_path() -> Path:
    return app_home() / "weather_config.json"


def launched_by_launcher() -> bool:
    return os.environ.get(LAUNCHER_ENV_VAR) == "1"
