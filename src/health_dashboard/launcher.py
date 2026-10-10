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
from collections.abc import Callable
from pathlib import Path

from health_dashboard.paths import HOME_ENV_VAR, LAUNCHER_ENV_VAR

APP_TITLE = "体調・睡眠分析ダッシュボード"
HOME_FOLDER_NAME = "体調分析ダッシュボード"
DEFAULT_PORT = 8765
PORT_TRIES = 20
STARTUP_TIMEOUT_SECONDS = 180  # 初回の、ウイルス対策ソフトの検査で遅くなるPCも想定
# 初期状態の設定ファイル（緯度・経度が null の間は、気象データを使わず通信もしない）。
WEATHER_CONFIG_TEMPLATE = {"latitude": None, "longitude": None}
SHORTCUT_NAME = "体調分析ダッシュボード.lnk"
SHORTCUT_MARKER_NAME = ".desktop_shortcut"
DESKTOP_ENV_VAR = "HEALTH_DASHBOARD_DESKTOP"  # デスクトップの場所の上書き（動作確認用）
NO_SHORTCUT_ENV_VAR = "HEALTH_DASHBOARD_NO_SHORTCUT"  # 1ならショートカットを作らない


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


def desktop_dir() -> Path | None:
    """実際のデスクトップのフォルダ（OneDriveに移されていれば、その場所）。取れなければNone。"""
    if os.environ.get(DESKTOP_ENV_VAR):
        return Path(os.environ[DESKTOP_ENV_VAR])
    if sys.platform != "win32":
        return None
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    # 0x0010 = CSIDL_DESKTOPDIRECTORY（Windowsが管理する、実際のデスクトップの場所）
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) != 0:
        return None
    return Path(buffer.value)


_SHORTCUT_SCRIPT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HD_LNK); "
    "$s.TargetPath = $env:HD_TARGET; $s.Arguments = $env:HD_ARGS; "
    "$s.WorkingDirectory = $env:HD_WORKDIR; $s.IconLocation = $env:HD_ICON + ',0'; "
    "$s.Description = $env:HD_DESCRIPTION; $s.Save()"
)


def create_shortcut(lnk: Path, target: Path, arguments: str, workdir: Path, icon: Path) -> bool:
    """Windowsのショートカット（.lnk）を作る。成功ならTrue。"""
    env = {
        **os.environ,
        "HD_LNK": str(lnk),
        "HD_TARGET": str(target),
        "HD_ARGS": arguments,
        "HD_WORKDIR": str(workdir),
        "HD_ICON": str(icon),
        "HD_DESCRIPTION": APP_TITLE,
    }
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SHORTCUT_SCRIPT],
        env=env,
        capture_output=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0 and lnk.exists()


def ensure_desktop_shortcut(
    app_dir: Path,
    home: Path,
    desktop: Path | None = None,
    creator: Callable[..., bool] = create_shortcut,
    recreate_if_missing: bool = False,
) -> bool:
    """デスクトップに、アプリを開くショートカットを作る（作った・更新したときTrue）。

    - 同梱のPython（app_dir/python/pythonw.exe）があるときだけ作る（開発中は作らない）。
    - どのフォルダのアプリを指しているかを、home の記録ファイルに残す。アプリを別の場所に
      置き直した・新しい版に入れ替えたときは、ショートカットの指す先を更新する。
    - 利用者がショートカットを消した場合は、作り直さない（記録だけが残っている状態）。
      ただし、recreate_if_missing（「体調分析を起動.bat」から起動したとき）は、作り直す。
    """
    target = app_dir / "python" / "pythonw.exe"
    icon = app_dir / "app.ico"
    desktop = desktop or desktop_dir()
    if desktop is None or not desktop.is_dir() or not target.is_file():
        return False

    lnk = desktop / SHORTCUT_NAME
    marker = home / SHORTCUT_MARKER_NAME
    recorded = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    if lnk.exists():
        if recorded == str(app_dir):
            return False
    elif recorded is not None and not recreate_if_missing:
        return False

    if not creator(lnk, target, "run_dashboard.py", app_dir, icon):
        return False
    marker.write_text(str(app_dir), encoding="utf-8")
    return True


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
    # run_server.py は、`streamlit run` を、ブラウザが閉じたら自動終了する監視つきで実行する。
    return [
        sys.executable,
        str(app_path.parent / "run_server.py"),
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


def main(app_path: Path, create_shortcut_if_missing: bool = False) -> int:
    home = default_home()
    prepare_home(home)
    log_path = home / "logs" / "launcher.log"

    port = DEFAULT_PORT
    if is_dashboard_running(port) or wait_if_starting(port):
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
        # アプリの準備ができるまでの間、真っ白な画面（または接続エラー）を見せないよう、
        # 「起動しています」の待機ページを先に開く。準備ができると、自動で本画面に切り替わる。
        loading_opened = open_loading_page(home, free)
        if not wait_until_running(free, process, STARTUP_TIMEOUT_SECONDS):
            process.terminate()
            show_error(
                f"{APP_TITLE}を起動できませんでした。\n"
                f"詳細は次のファイルに記録されています。\n{log_path}"
            )
            return 1
        if not loading_opened:
            open_browser(free)
        make_desktop_shortcut(app_path.parent, home, create_shortcut_if_missing)
        return process.wait()


def wait_if_starting(port: int, timeout: float = 60.0) -> bool:
    """そのポートを別の起動処理が使い始めている（準備中）なら、準備ができるまで待つ。

    ダブルクリックを続けて2回した場合に、2つ目のサーバーを別のポートで起動してしまわないため。
    別のアプリがポートを使っているだけなら、timeoutまで待ってFalseを返す。
    """
    if is_port_free(port):
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_dashboard_running(port):
            return True
        time.sleep(0.5)
    return False


_LOADING_PAGE = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<title>起動しています - 体調・睡眠分析ダッシュボード</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center;
         justify-content: center; background: #f6f8f8; color: #26373a;
         font-family: "Yu Gothic UI", "Meiryo", sans-serif; }
  main { text-align: center; padding: 24px; }
  .spinner { width: 48px; height: 48px; margin: 0 auto 24px; border-radius: 50%;
             border: 5px solid #cfe3e1; border-top-color: #2f8f83;
             animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  h1 { font-size: 22px; margin: 0 0 12px; }
  p { margin: 6px 0; line-height: 1.7; }
  #slow { margin-top: 24px; color: #8a5a00; }
</style>
</head>
<body>
<main>
  <div class="spinner"></div>
  <h1>体調・睡眠分析ダッシュボードを起動しています</h1>
  <p>準備ができると、自動で画面が切り替わります。</p>
  <p>初回は30秒ほどかかることがあります。このままお待ちください。</p>
  <p id="slow" hidden>時間がかかっています。切り替わらないときは、
    <a id="link" href="http://127.0.0.1:__PORT__/">こちら</a>を押してください。</p>
</main>
<script>
  var base = "http://127.0.0.1:__PORT__/";
  function check() {
    var image = new Image();
    image.onload = function () { location.replace(base); };
    image.onerror = function () { setTimeout(check, 1000); };
    image.src = base + "favicon.png?t=" + Date.now();
  }
  check();
  setTimeout(function () { document.getElementById("slow").hidden = false; }, 60000);
</script>
</body>
</html>
"""


def write_loading_page(home: Path, port: int) -> Path:
    """待機ページ（アプリの準備ができたら本画面へ自動で切り替わるHTML）を書き出す。"""
    page = home / "loading.html"
    page.write_text(_LOADING_PAGE.replace("__PORT__", str(port)), encoding="utf-8")
    return page


def open_loading_page(home: Path, port: int) -> bool:
    """待機ページをブラウザで開く。開けたらTrue（開けなければ、準備後に本画面を直接開く）。"""
    if os.environ.get("HEALTH_DASHBOARD_NO_BROWSER") == "1":
        return True
    try:
        return bool(webbrowser.open(write_loading_page(home, port).as_uri()))
    except Exception:  # noqa: BLE001 - 開けなくても、準備後に本画面を直接開けばよい
        return False


def make_desktop_shortcut(app_dir: Path, home: Path, recreate_if_missing: bool = False) -> None:
    """デスクトップのショートカットを用意する。失敗しても、アプリの起動には影響させない。"""
    if sys.platform != "win32" or os.environ.get(NO_SHORTCUT_ENV_VAR) == "1":
        return
    try:
        ensure_desktop_shortcut(app_dir, home, recreate_if_missing=recreate_if_missing)
    except Exception:  # noqa: BLE001 - ショートカットは便利機能なので、失敗しても無視する
        pass


def open_browser(port: int) -> None:
    if os.environ.get("HEALTH_DASHBOARD_NO_BROWSER") == "1":
        return
    webbrowser.open(f"http://127.0.0.1:{port}")
