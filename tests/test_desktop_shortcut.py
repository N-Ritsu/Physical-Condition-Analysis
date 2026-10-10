import pytest

from health_dashboard import launcher


class _Creator:
    """ショートカットを作る処理の代わり。呼ばれた内容を記録し、ファイルだけ作る。"""

    def __init__(self, succeed=True):
        self.calls = []
        self.succeed = succeed

    def __call__(self, lnk, target, arguments, workdir, icon):
        self.calls.append((lnk, target, arguments, workdir, icon))
        if self.succeed:
            lnk.write_bytes(b"lnk")
        return self.succeed


@pytest.fixture
def env(tmp_path):
    app = tmp_path / "app_0.2.0"
    (app / "python").mkdir(parents=True)
    (app / "python" / "pythonw.exe").write_bytes(b"exe")
    home = tmp_path / "home"
    home.mkdir()
    desktop = tmp_path / "desktop"
    desktop.mkdir()
    return app, home, desktop


def test_first_launch_creates_shortcut_pointing_at_bundled_pythonw(env):
    app, home, desktop = env
    creator = _Creator()

    assert launcher.ensure_desktop_shortcut(app, home, desktop, creator) is True

    lnk, target, arguments, workdir, icon = creator.calls[0]
    assert lnk == desktop / launcher.SHORTCUT_NAME
    assert target == app / "python" / "pythonw.exe"
    assert arguments == "run_dashboard.py"
    assert workdir == app
    assert icon == app / "app.ico"
    assert (home / launcher.SHORTCUT_MARKER_NAME).read_text(encoding="utf-8") == str(app)


def test_second_launch_does_nothing(env):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)

    assert launcher.ensure_desktop_shortcut(app, home, desktop, creator) is False
    assert len(creator.calls) == 1


def test_shortcut_deleted_by_user_is_not_recreated(env):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)
    (desktop / launcher.SHORTCUT_NAME).unlink()

    assert launcher.ensure_desktop_shortcut(app, home, desktop, creator) is False
    assert not (desktop / launcher.SHORTCUT_NAME).exists()


def test_deleted_shortcut_is_recreated_when_started_from_the_bat_file(env):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)
    (desktop / launcher.SHORTCUT_NAME).unlink()

    created = launcher.ensure_desktop_shortcut(
        app, home, desktop, creator, recreate_if_missing=True
    )

    assert created is True
    assert (desktop / launcher.SHORTCUT_NAME).exists()


def test_existing_shortcut_is_left_alone_even_when_started_from_the_bat_file(env):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)

    created = launcher.ensure_desktop_shortcut(
        app, home, desktop, creator, recreate_if_missing=True
    )

    assert created is False
    assert len(creator.calls) == 1


def test_shortcut_is_updated_when_app_folder_changes(env, tmp_path):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)
    new_app = tmp_path / "app_0.3.0"
    (new_app / "python").mkdir(parents=True)
    (new_app / "python" / "pythonw.exe").write_bytes(b"exe")

    assert launcher.ensure_desktop_shortcut(new_app, home, desktop, creator) is True

    assert creator.calls[-1][1] == new_app / "python" / "pythonw.exe"
    assert (home / launcher.SHORTCUT_MARKER_NAME).read_text(encoding="utf-8") == str(new_app)


def test_old_shortcut_deleted_then_new_version_is_not_recreated(env, tmp_path):
    app, home, desktop = env
    creator = _Creator()
    launcher.ensure_desktop_shortcut(app, home, desktop, creator)
    (desktop / launcher.SHORTCUT_NAME).unlink()
    new_app = tmp_path / "app_0.3.0"
    (new_app / "python").mkdir(parents=True)
    (new_app / "python" / "pythonw.exe").write_bytes(b"exe")

    assert launcher.ensure_desktop_shortcut(new_app, home, desktop, creator) is False


def test_no_shortcut_without_bundled_python(env, tmp_path):
    _, home, desktop = env
    dev_dir = tmp_path / "repo"
    dev_dir.mkdir()
    creator = _Creator()

    assert launcher.ensure_desktop_shortcut(dev_dir, home, desktop, creator) is False
    assert creator.calls == []


def test_failed_creation_leaves_no_marker_so_it_is_retried(env):
    app, home, desktop = env

    assert launcher.ensure_desktop_shortcut(app, home, desktop, _Creator(succeed=False)) is False

    assert not (home / launcher.SHORTCUT_MARKER_NAME).exists()
    assert launcher.ensure_desktop_shortcut(app, home, desktop, _Creator()) is True


def test_missing_desktop_folder_is_skipped(env, tmp_path):
    app, home, _ = env

    assert launcher.ensure_desktop_shortcut(app, home, tmp_path / "nowhere", _Creator()) is False


def test_desktop_dir_respects_override(monkeypatch, tmp_path):
    monkeypatch.setenv(launcher.DESKTOP_ENV_VAR, str(tmp_path))

    assert launcher.desktop_dir() == tmp_path


def test_make_desktop_shortcut_never_raises(env, monkeypatch):
    app, home, _ = env
    monkeypatch.setattr(launcher.sys, "platform", "win32")

    def boom(*_args, **_kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(launcher, "ensure_desktop_shortcut", boom)

    launcher.make_desktop_shortcut(app, home)  # 例外を出さない


def test_make_desktop_shortcut_can_be_disabled(env, monkeypatch):
    app, home, _ = env
    monkeypatch.setattr(launcher.sys, "platform", "win32")
    monkeypatch.setenv(launcher.NO_SHORTCUT_ENV_VAR, "1")
    called = []
    monkeypatch.setattr(launcher, "ensure_desktop_shortcut", lambda *a, **k: called.append(1))

    launcher.make_desktop_shortcut(app, home)

    assert called == []
