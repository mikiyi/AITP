"""严格类型门禁：mypy --strict 扫全后端，模拟前端 vue-tsc --noEmit。

用法：
    python backend/scripts/check_types.py
退出码 0 = 无类型错误。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    config = ROOT / "mypy.ini"
    cmd = [
        sys.executable, "-m", "mypy",
        "--config-file", str(config),
        str(ROOT / "backend" / "aitp"),
        str(ROOT / "backend" / "scripts"),
        "--no-pretty",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(ROOT))
    except FileNotFoundError:
        print("[FAIL] 未安装 mypy，请先执行：pip install -r requirements.txt", flush=True)
        return 1
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in output.splitlines() if line.strip()]
    if proc.returncode != 0:
        for line in lines:
            print(line, flush=True)
        print(f"\n[FAIL] mypy --strict 存在类型错误（退出码 {proc.returncode}）", flush=True)
        return 1
    print("[PASS] mypy --strict 全后端零类型错误", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
