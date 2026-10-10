import json
import socket
import sys
from pathlib import Path

import pytest

from health_dashboard import launcher, paths


def test_prepare_home_creates_data_logs_and_weather_template(tmp_path):
    home = tmp_path / "home"

    launcher.prepare_home(home)

    assert (home / "data").is_dir()
    assert (home / "logs").is_dir()
    config = json.loads((home / "weather_config.json").read_text(encoding="utf-8"))
    assert config == {"latitude": None, "longitude": None}


def test_prepare_home_never_overwrites_existing_files(tmp_path):
    home = tmp_path / "home"
    (home / "data" / "西村").mkdir(parents=True)
    (home / "data" / "西村" / "日報.xlsx").write_text("keep")
    (home / "weather_config.json").write_text('{"latitude": 35.0, "longitude": 139.0}')

    launcher.prepare_home(home)

    assert (home / "data" / "西村" / "日報.xlsx").read_text() == "keep"
    assert json.loads((home / "weather_config.json").read_text())["latitude"] == 35.0


def test_default_home_is_local_app_data_not_documents_and_respects_override(
    tmp_path, monkeypatch
):
    local = tmp_path / "AppData" / "Local"
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local))

    home = launcher.default_home()

    assert home == local / "体調分析ダッシュボード"
    # OneDriveに同期されうる「ドキュメント」「デスクトップ」の下には置かない。
    assert "Documents" not in home.parts and "OneDrive" not in home.parts

    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    assert launcher.default_home() == tmp_path


def test_default_home_without_local_app_data_falls_back_outside_documents(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    home = launcher.default_home()

    assert home == Path.home() / ".local" / "share" / "体調分析ダッシュボード"


def _occupy_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_find_free_port_skips_occupied_ports():
    sock, port = _occupy_port()
    try:
        assert launcher.is_port_free(port) is False
        found = launcher.find_free_port(port, tries=5)
        assert found is not None and found != port
        assert launcher.is_port_free(found) is True
    finally:
        sock.close()


def test_find_free_port_returns_none_when_nothing_is_free():
    sock, port = _occupy_port()
    try:
        assert launcher.find_free_port(port, tries=1) is None
    finally:
        sock.close()


def test_is_dashboard_running_false_when_nothing_listens():
    sock, port = _occupy_port()
    sock.close()  # 閉じた直後のポートには何も待ち受けていない

    assert launcher.is_dashboard_running(port) is False


def test_streamlit_command_is_local_only_and_disables_telemetry():
    command = launcher.build_streamlit_command(Path("/app/app.py"), 8800)

    assert command[0] == sys.executable
    # 自動終了の監視つきで streamlit run を実行する入口（app.py と同じフォルダ）
    assert Path(command[1]) == Path("/app/run_server.py")  # Windowsでは区切りが \\ になる
    assert command[2] == "run"
    assert Path(command[3]) == Path("/app/app.py")
    options = dict(zip(command[4::2], command[5::2], strict=True))
    assert options["--server.address"] == "127.0.0.1"
    assert options["--server.port"] == "8800"
    assert options["--server.headless"] == "true"
    assert options["--browser.gatherUsageStats"] == "false"
    assert options["--client.showErrorDetails"] == "none"


def test_loading_page_polls_the_app_port_and_switches_automatically(tmp_path):
    page = launcher.write_loading_page(tmp_path, 8791)

    html = page.read_text(encoding="utf-8")

    assert page.name == "loading.html"
    assert '"http://127.0.0.1:8791/"' in html  # 切り替え先
    assert "favicon.png" in html  # 準備ができたかの確認に使う
    assert "__PORT__" not in html
    assert "起動しています" in html


def test_loading_page_is_not_opened_when_browser_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTH_DASHBOARD_NO_BROWSER", "1")
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)

    assert launcher.open_loading_page(tmp_path, 8791) is True
    assert opened == []


def test_loading_page_opens_as_a_file_url(tmp_path, monkeypatch):
    monkeypatch.delenv("HEALTH_DASHBOARD_NO_BROWSER", raising=False)
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url) or True)

    assert launcher.open_loading_page(tmp_path, 8791) is True
    assert opened == [(tmp_path / "loading.html").as_uri()]


def test_loading_page_failure_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("HEALTH_DASHBOARD_NO_BROWSER", raising=False)

    def fail(_url):
        raise OSError("no browser")

    monkeypatch.setattr(launcher.webbrowser, "open", fail)

    assert launcher.open_loading_page(tmp_path, 8791) is False


def test_wait_if_starting_returns_false_when_port_is_free(monkeypatch):
    monkeypatch.setattr(launcher, "is_port_free", lambda port: True)

    assert launcher.wait_if_starting(8791, timeout=0.2) is False


def test_wait_if_starting_waits_for_a_starting_dashboard(monkeypatch):
    answers = iter([False, False, True])
    monkeypatch.setattr(launcher, "is_port_free", lambda port: False)
    monkeypatch.setattr(launcher, "is_dashboard_running", lambda port: next(answers))
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)

    assert launcher.wait_if_starting(8791, timeout=5) is True


def test_wait_if_starting_gives_up_for_another_program_on_the_port(monkeypatch):
    monkeypatch.setattr(launcher, "is_port_free", lambda port: False)
    monkeypatch.setattr(launcher, "is_dashboard_running", lambda port: False)

    assert launcher.wait_if_starting(8791, timeout=0.2) is False


def test_paths_follow_home_override(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))

    assert paths.app_home() == tmp_path
    assert paths.data_dir() == tmp_path / "data"
    assert paths.weather_config_path() == tmp_path / "weather_config.json"


def test_paths_default_to_project_root(monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)

    assert (paths.app_home() / "app.py").exists()


@pytest.mark.parametrize(("value", "expected"), [("1", True), ("0", False), (None, False)])
def test_launched_by_launcher_flag(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv(paths.LAUNCHER_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(paths.LAUNCHER_ENV_VAR, value)

    assert paths.launched_by_launcher() is expected
