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


def test_default_home_is_under_documents_and_respects_override(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.HOME_ENV_VAR, raising=False)
    assert launcher.default_home() == Path.home() / "Documents" / "体調分析ダッシュボード"

    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    assert launcher.default_home() == tmp_path


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

    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert "/app/app.py" in command[4]
    options = dict(zip(command[5::2], command[6::2], strict=True))
    assert options["--server.address"] == "127.0.0.1"
    assert options["--server.port"] == "8800"
    assert options["--server.headless"] == "true"
    assert options["--browser.gatherUsageStats"] == "false"
    assert options["--client.showErrorDetails"] == "none"


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
