"""Streamlitのサーバーを起動する（run_dashboard.py から呼ばれる。直接使うことは想定しない）。

`python -m streamlit run ...` と同じだが、次の2つを加えている。

- サーバーの起動直後から、ブラウザが全て閉じられたら自動で終了する監視を始める。
  アプリの画面（app.py）が最初に描画されるのを待たないので、起動直後の読み込み中に
  ブラウザを閉じても、サーバーが残らない。
- 画面の表示に使う重いライブラリ（pandas・numpy・plotlyなど）を、サーバーを起動する前に
  読み込んでおく。最初の画面が出るまでの真っ白な時間を短くするため（読み込みを先に
  済ませておかないと、ブラウザが接続した後の最初の1回で、その時間がかかる）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from health_dashboard import paths  # noqa: E402
from health_dashboard.idle_shutdown import start_idle_watcher  # noqa: E402


def _preload_heavy_libraries() -> None:
    """画面の表示で使うライブラリを先に読み込む。失敗しても、アプリの動作には影響しない。"""
    try:
        import numpy  # noqa: F401
        import openpyxl  # noqa: F401
        import pandas  # noqa: F401
        import plotly.graph_objects as go

        # 最初のグラフ作成で時間がかかる部品（入力検査の定義など）も読み込んでおく。
        go.Figure(go.Scatter(x=[0], y=[0]))

        import health_dashboard.border_analysis  # noqa: F401
        import health_dashboard.condition_scheme  # noqa: F401
        import health_dashboard.data_loader  # noqa: F401
        import health_dashboard.stats  # noqa: F401
        import health_dashboard.weather  # noqa: F401
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    if paths.launched_by_launcher():
        # サーバーを起動する前に、順番に読み込む（別スレッドで並行させると、pandasの読み込み途中に
        # plotlyが触れて失敗することがあった）。この間は、ブラウザには待機ページが出ている。
        _preload_heavy_libraries()

    from streamlit.web.cli import main

    if paths.launched_by_launcher():
        start_idle_watcher()

    sys.exit(main(prog_name="streamlit"))
