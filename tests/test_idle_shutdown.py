import threading

import pytest

from health_dashboard.idle_shutdown import (
    GRACE_ENV_VAR,
    IdleMonitor,
    active_session_count,
    start_idle_watcher,
)


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _monitor(counts, clock, grace=120, poll=5):
    """counts は、監視のたびに返す接続数（None=不明）のリスト。足りなければ最後の値を使う。"""
    values = list(counts)

    def count():
        return values.pop(0) if len(values) > 1 else values[0]

    return IdleMonitor(count, grace_seconds=grace, poll_seconds=poll, clock=clock)


def _run(monitor, clock, seconds, step=5):
    """step秒ごとに監視して、終了すべきになった時点の経過秒を返す（ならなければNone）。"""
    for elapsed in range(step, seconds + 1, step):
        clock.advance(step)
        if monitor.should_exit():
            return elapsed
    return None


def test_does_not_exit_while_a_browser_is_connected():
    clock = _Clock()
    monitor = _monitor([1], clock)

    assert _run(monitor, clock, 3600) is None


def test_exits_after_grace_once_all_browsers_are_closed():
    clock = _Clock()
    monitor = _monitor([1, 1, 0], clock)  # 2回目まで接続あり、その後は切断

    elapsed = _run(monitor, clock, 600)

    # 切断を最初に確認した時点（3回目=15秒）から120秒後
    assert elapsed == 15 + 120


def test_reconnecting_within_grace_cancels_the_exit():
    clock = _Clock()
    counts = {"value": 0}
    monitor = IdleMonitor(lambda: counts["value"], 120, 5, clock)

    assert _run(monitor, clock, 100) is None  # 100秒間、接続なし（猶予内）
    counts["value"] = 1  # 開き直した
    assert _run(monitor, clock, 100) is None
    counts["value"] = 0
    assert _run(monitor, clock, 100) is None  # 数え直しなので、まだ終了しない
    assert _run(monitor, clock, 60) is not None


def test_unknown_session_count_never_exits():
    clock = _Clock()
    monitor = _monitor([None], clock)

    assert _run(monitor, clock, 3600) is None


def test_sleep_gap_is_not_counted_as_idle_time():
    clock = _Clock()
    counts = {"value": 0}
    monitor = IdleMonitor(lambda: counts["value"], 120, 5, clock)
    assert _run(monitor, clock, 60) is None  # 切断から60秒

    clock.advance(3 * 3600)  # スリープ（監視が止まっていた）
    assert monitor.should_exit() is False  # 復帰直後は、すぐには終了しない
    counts["value"] = 1  # ブラウザが再接続した
    assert _run(monitor, clock, 600) is None


def test_active_session_count_is_none_outside_streamlit_server():
    assert active_session_count() is None


def test_watcher_never_exits_when_sessions_cannot_be_counted():
    called = threading.Event()

    start_idle_watcher(
        on_idle=called.set,
        count_sessions=lambda: None,
        grace_seconds=0,
        poll_seconds=0.01,
        ready_timeout_seconds=0.2,
    )

    assert not called.wait(timeout=0.6)


def test_watcher_thread_calls_on_idle_when_no_browser(monkeypatch):
    monkeypatch.setenv(GRACE_ENV_VAR, "0")
    called = threading.Event()

    # ブラウザが一度も接続しなかった場合（接続数が最初から0）も終了する
    start_idle_watcher(on_idle=called.set, count_sessions=lambda: 0, poll_seconds=0.01)

    assert called.wait(timeout=5)


def test_watcher_waits_for_server_to_become_ready_then_monitors(monkeypatch):
    monkeypatch.setenv(GRACE_ENV_VAR, "0")
    called = threading.Event()
    answers = iter([None, None, None])

    def count():
        return next(answers, 0)  # 最初の数回は「サーバーの準備中」、その後は接続0

    start_idle_watcher(on_idle=called.set, count_sessions=count, poll_seconds=0.01)

    assert called.wait(timeout=5)


def test_grace_env_var_with_bad_value_is_ignored(monkeypatch):
    monkeypatch.setenv(GRACE_ENV_VAR, "abc")
    called = threading.Event()

    start_idle_watcher(on_idle=called.set, count_sessions=lambda: 1, poll_seconds=0.01)

    assert not called.wait(timeout=0.3)  # 接続ありなので終了しない（例外も出ない）


@pytest.mark.parametrize("count", [1, 5])
def test_watcher_does_not_exit_while_connected(count):
    called = threading.Event()

    start_idle_watcher(
        on_idle=called.set, count_sessions=lambda: count, grace_seconds=0, poll_seconds=0.01
    )

    assert not called.wait(timeout=0.3)
