"""支援員向けの起動スクリプト。「体調分析を起動.bat」から呼ばれる。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from health_dashboard.launcher import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(ROOT / "app.py"))
