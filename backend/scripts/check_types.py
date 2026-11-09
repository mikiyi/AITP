"""严格类型门禁：mypy --strict 扫全后端 + 真实执行前端 vue-tsc --noEmit。

mypy 缺失或类型报错一律 FAIL。前端部分在 frontend/package.json 存在时，
用 node 直接执行 node_modules/vue-tsc 的 bin 跑 `vue-tsc --noEmit`；
前端工程缺失、无 node 或依赖未安装时打印 [SKIP] 原因并默认返回 0，
CI 可用 --require-frontend 让缺前端依赖时直接 FAIL。

用法：
    python backend/scripts/check_types.py [--require-frontend]
退出码 0 = 无类型错误（或缺前端依赖且未要求必须检查）。
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


def check_mypy() -> int:
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


def check_vue_tsc(require_frontend: bool) -> int:
    """真实执行前端类型检查 vue-tsc --noEmit。"""
    def skip(reason: str) -> int:
        print(f"[SKIP] 前端 vue-tsc：{reason}", flush=True)
        if require_frontend:
            print("[FAIL] --require-frontend：缺前端依赖即视为门禁失败", flush=True)
            return 1
        return 0

    pkg = FRONTEND / "package.json"
    if not pkg.exists():
        return skip(f"未找到 {pkg.relative_to(ROOT)}，本仓库无前端工程")
    tsc_bin = FRONTEND / "node_modules" / "vue-tsc" / "bin" / "vue-tsc.js"
    if not tsc_bin.exists():
        return skip("前端依赖未安装（先执行：cd frontend && npm install）")
    node = shutil.which("node")
    if node is None:
        return skip("环境无 node，无法执行 vue-tsc")

    cmd = [node, str(tsc_bin), "--noEmit"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(FRONTEND), timeout=900)
    except subprocess.TimeoutExpired:
        print("[FAIL] 前端 vue-tsc --noEmit 超时（>900s）", flush=True)
        return 1
    output = (proc.stdout or "") + (proc.stderr or "")
    lines = [line for line in output.splitlines() if line.strip()]
    if proc.returncode != 0:
        for line in lines:
            print(line, flush=True)
        print(f"\n[FAIL] vue-tsc --noEmit 存在类型错误（退出码 {proc.returncode}）", flush=True)
        return 1
    print("[PASS] 前端 vue-tsc --noEmit 零类型错误", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="mypy --strict + 真实 vue-tsc 类型门禁")
    parser.add_argument("--require-frontend", action="store_true",
                        help="缺前端工程/依赖或无 node 时直接 FAIL（供 CI 使用）")
    args = parser.parse_args()
    rc = check_mypy()
    rc |= check_vue_tsc(bool(args.require_frontend))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
