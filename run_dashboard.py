"""支援員向けの起動スクリプト。「体調分析を起動.bat」とデスクトップのショートカットから呼ばれる。

--create-shortcut（.batが付ける）: デスクトップのショートカットが無ければ作り直す。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from health_dashboard.launcher import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(ROOT / "app.py", "--create-shortcut" in sys.argv[1:]))
