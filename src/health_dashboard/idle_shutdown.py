"""ブラウザが全て閉じられたら、アプリを自動で終了する（ダブルクリック起動版用）。

ダブルクリック起動では、アプリ本体は黒い画面なしで裏で動く。ブラウザのタブを閉じても
止まらないため、見えないまま動き続けたり、アプリの入れ替えができなくなったりする。
そこで、アプリに接続しているブラウザが無い状態が一定時間続いたら、自分で終了する。

- 接続数が取れない（Streamlitの内部の仕組みが変わった等）ときは、自動終了は行わない
  （終了ボタンだけで運用できる状態に戻る。誤って終了してしまうよりも安全）。
- パソコンのスリープ中は監視の時計が飛ぶため、飛んだ分は「ブラウザが閉じていた時間」に
  数えない（復帰後にブラウザが再接続する前に、終了してしまわないため）。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

# ブラウザが全て閉じてから終了するまでの猶予。タブを閉じてすぐ開き直す、画面を再読み込み
# する、といった操作で終了しないための時間。
IDLE_GRACE_SECONDS = 120
GRACE_ENV_VAR = "HEALTH_DASHBOARD_IDLE_GRACE"
POLL_INTERVAL_SECONDS = 5
# サーバーの起動（Streamlitの準備）を待つ上限。これを過ぎたら、自動終了は諦める。
READY_TIMEOUT_SECONDS = 600
# 監視の間隔がこの倍数より空いたら、スリープなどで止まっていたとみなす。
_SUSPEND_FACTOR = 3


def active_session_count() -> int | None:
    """アプリに接続しているブラウザ（画面）の数。取れなければNone。"""
    try:
        from streamlit.runtime import Runtime

        if not Runtime.exists():
            return None
        return int(Runtime.instance()._session_mgr.num_active_sessions())
    except Exception:  # noqa: BLE001 - Streamlitの内部仕様が変わっても、アプリは止めない
        return None


class IdleMonitor:
    """接続が無い状態が続いた時間を数える（時計や終了処理は外から渡せる：テスト用）。"""

    def __init__(
        self,
        count_sessions: Callable[[], int | None],
        grace_seconds: float = IDLE_GRACE_SECONDS,
        poll_seconds: float = POLL_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._count_sessions = count_sessions
        self._grace = grace_seconds
        self._poll = poll_seconds
        self._clock = clock
        now = clock()
        self._last_tick = now
        self._idle_since: float | None = None

    def should_exit(self) -> bool:
        """1回分の監視。ブラウザが無い状態が猶予を超えたらTrue。"""
        now = self._clock()
        suspended = now - self._last_tick > self._poll * _SUSPEND_FACTOR
        self._last_tick = now

        count = self._count_sessions()
        if count is None or count > 0:
            # 接続あり、または不明（終了してよいか分からない）。
            self._idle_since = None
            return False
        if suspended or self._idle_since is None:
            # 接続が無くなった最初の確認、またはスリープ明け。ここから数え直す。
            self._idle_since = now
            return False
        return now - self._idle_since >= self._grace


def start_idle_watcher(
    on_idle: Callable[[], None] | None = None,
    count_sessions: Callable[[], int | None] = active_session_count,
    grace_seconds: float = IDLE_GRACE_SECONDS,
    poll_seconds: float = POLL_INTERVAL_SECONDS,
    ready_timeout_seconds: float = READY_TIMEOUT_SECONDS,
) -> None:
    """ブラウザの接続を監視するスレッドを始める（サーバーの起動時に呼ぶ）。

    ブラウザが一度も接続しなくても、猶予を過ぎれば終了する。接続数が取れる状態に
    ならなければ（ready_timeout_seconds以内）、自動終了は行わない。
    """
    try:  # 動作確認用に、猶予時間（秒）を環境変数で変えられる
        grace_seconds = float(os.environ.get(GRACE_ENV_VAR, grace_seconds))
    except ValueError:
        pass
    if on_idle is None:

        def on_idle() -> None:
            # ログ（launcher.log）で文字化けしないよう、ASCIIで書く。
            print("All browser windows were closed. Shutting down.", flush=True)
            os._exit(0)

    def watch() -> None:
        # サーバーの準備ができる（接続数が取れるようになる）まで待ってから、監視を始める。
        # 最初の画面が描画される前にブラウザが閉じられても、終了できるようにするため。
        deadline = time.monotonic() + ready_timeout_seconds
        while count_sessions() is None:
            if time.monotonic() > deadline:
                return  # 接続数が取れない環境。自動終了は行わない。
            time.sleep(min(1.0, poll_seconds))
        monitor = IdleMonitor(count_sessions, grace_seconds, poll_seconds)
        while True:
            time.sleep(poll_seconds)
            if monitor.should_exit():
                on_idle()
                return

    threading.Thread(target=watch, name="idle-shutdown", daemon=True).start()
