"""支援員向けの起動プログラム（ダブルクリックで起動する用）。

コマンドを打たなくても、ダッシュボードを起動してブラウザで開く。

- データ・設定の置き場所（%LOCALAPPDATA% の「体調分析ダッシュボード」フォルダ）を用意する。
- すでに起動していれば、2つ目は起動せず、ブラウザだけ開く。
- 自分のPC内だけ（127.0.0.1）で待ち受け、利用状況の自動送信を止める。
- 起動に失敗したときは、黒い画面が無くても分かるよう、メッセージ画面とログで知らせる。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from health_dashboard.paths import HOME_ENV_VAR, LAUNCHER_ENV_VAR

APP_TITLE = "体調・睡眠分析ダッシュボード"
HOME_FOLDER_NAME = "体調分析ダッシュボード"
DEFAULT_PORT = 8765
PORT_TRIES = 20
STARTUP_TIMEOUT_SECONDS = 90
# 初期状態の設定ファイル（緯度・経度が null の間は、気象データを使わず通信もしない）。
WEATHER_CONFIG_TEMPLATE = {"latitude": None, "longitude": None}


def default_home() -> Path:
    """配布版でデータ・設定を置くフォルダ。

    「ドキュメント」ではなく、PC内だけにある %LOCALAPPDATA%（AppData/Local）に置く。
    ドキュメント・デスクトップは、OneDriveの「フォルダーのバックアップ」で自動的にクラウドへ
    同期されることがあり、利用者の健康データが外部に送られてしまうため。
    """
    if os.environ.get(HOME_ENV_VAR):
        return Path(os.environ[HOME_ENV_VAR])
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / HOME_FOLDER_NAME
    return Path.home() / ".local" / "share" / HOME_FOLDER_NAME


def prepare_home(home: Path) -> None:
    """データ置き場と設定ファイルが無ければ作る。既にあるものは変更しない。"""
    (home / "data").mkdir(parents=True, exist_ok=True)
    (home / "logs").mkdir(parents=True, exist_ok=True)
    config = home / "weather_config.json"
    if not config.exists():
        config.write_text(
            json.dumps(WEATHER_CONFIG_TEMPLATE, indent=2) + "\n", encoding="utf-8"
        )


def is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def find_free_port(start: int = DEFAULT_PORT, tries: int = PORT_TRIES) -> int | None:
    for port in range(start, start + tries):
        if is_port_free(port):
            return port
    return None


def is_dashboard_running(port: int) -> bool:
    """そのポートで、Streamlitのアプリが応答しているか。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
            return r.status == 200 and r.read().strip() == b"ok"
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def build_streamlit_command(app_path: Path, port: int) -> list[str]:
    """支援員向けの設定で Streamlit を起動するコマンド。"""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--server.fileWatcherType",
        "none",
        "--browser.gatherUsageStats",
        "false",
        "--client.toolbarMode",
        "minimal",
        "--client.showErrorDetails",
        "none",
    ]


def show_error(message: str) -> None:
    """黒い画面が出ない起動でも気づけるよう、可能ならメッセージ画面で知らせる。"""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    else:
        print(message, file=sys.stderr)


def wait_until_running(port: int, process: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if is_dashboard_running(port):
            return True
        time.sleep(0.5)
    return False


def main(app_path: Path) -> int:
    home = default_home()
    prepare_home(home)
    log_path = home / "logs" / "launcher.log"

    port = DEFAULT_PORT
    if is_dashboard_running(port):
        open_browser(port)
        return 0
    free = find_free_port(port)
    if free is None:
        show_error(f"{APP_TITLE}を起動できませんでした（使えるポートがありません）。")
        return 1

    env = {**os.environ, HOME_ENV_VAR: str(home), LAUNCHER_ENV_VAR: "1"}
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(log_path, "a", encoding="utf-8") as log:
        process = subprocess.Popen(
            build_streamlit_command(app_path, free),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        if not wait_until_running(free, process, STARTUP_TIMEOUT_SECONDS):
            process.terminate()
            show_error(
                f"{APP_TITLE}を起動できませんでした。\n"
                f"詳細は次のファイルに記録されています。\n{log_path}"
            )
            return 1
        open_browser(free)
        return process.wait()


def open_browser(port: int) -> None:
    if os.environ.get("HEALTH_DASHBOARD_NO_BROWSER") == "1":
        return
    webbrowser.open(f"http://127.0.0.1:{port}")
