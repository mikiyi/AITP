"""门禁一键 runner（GAP-21 门禁工程化）。

自动发现 backend/scripts 下全部 check_*.py 加 smoke_api.py，按名字典序逐个用
subprocess 执行，汇总每个脚本的退出码与 PASS/FAIL 计数，末尾打印一张汇总表。

    python backend/scripts/run_all_checks.py                # 全量
    python backend/scripts/run_all_checks.py --list          # 只列清单不执行
    python backend/scripts/run_all_checks.py --only check_types,check_db
    python backend/scripts/run_all_checks.py --skip-name check_walkthrough
    python backend/scripts/run_all_checks.py --timeout 600 --json logs/gates.json

要点：
- check_ai_live 永久排除：它打真实商汤模型接口、烧额度且可能长时间挂住，
  不是确定性门禁，需要时请手工运行。
- check_isolation_matrix（及被排除的 check_ai_live）直接打已启动的本地服务；
  其余脚本（含 smoke_api）用 Flask test_client 在临时库上自跑，不依赖服务。
  runner 会先探测 /api/v1/health；探测不到时明确警告，并把需要服务的脚本
  记为 FAIL（原因：需先启动本地服务），绝不自动启动服务、绝不假装通过。
- 新增门禁脚本只要放进 backend/scripts 且命名 check_*.py 即自动纳入，无需改本文件。

退出码：全部脚本通过 = 0；任何 FAIL/TIMEOUT = 1。
"""

from __future__ import annotations

import argparse
import atexit
import datetime
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
cast(Any, sys.stderr).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "backend" / "scripts"

# check_ai_live 打真实大模型接口、烧额度、非确定性，永久排除出聚合门禁。
HARD_EXCLUDED = {"check_ai_live"}

# 需要本地服务已在跑（直接发 HTTP 到 AITP_BASE_URL）的脚本；
# 其余脚本自建临时库 + test_client，服务没起也能跑。
SERVICE_DEPENDENT = {"check_isolation_matrix"}

# 现有脚本末尾汇总行形如 "PASS=12 FAIL=0"、"合计 PASS=n FAIL=n"、
# "提示词校验: PASS=n FAIL=n"，解析不到再退回 [PASS]/[FAIL] 行计数。
COUNT_RE = re.compile(r"PASS\s*=\s*(\d+)[^\n]*?FAIL\s*=\s*(\d+)")

DEFAULT_BASE_URL = "http://127.0.0.1:8760"  # backend/aitp/config.py 默认 AITP_PORT=8760


def discover() -> list[str]:
    """返回脚本名（不含 .py）列表，按字典序；含 smoke_api，排除 HARD_EXCLUDED。"""
    names = sorted(
        p.stem for p in SCRIPTS_DIR.glob("check_*.py")
        if p.stem not in HARD_EXCLUDED
    )
    if (SCRIPTS_DIR / "smoke_api.py").exists():
        names.append("smoke_api")
    return names


def apply_filters(names: list[str], only: list[str], skip: list[str]) -> list[str]:
    def keep(n: str) -> bool:
        if only and not any(f in n for f in only):
            return False
        if any(s in n for s in skip):
            return False
        return True
    return [n for n in names if keep(n)]


def probe_health(base_url: str) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return (200 <= resp.status < 300), f"{url} -> {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"{url} -> HTTP {exc.code}"
    except Exception as exc:  # URLError / ConnectionRefusedError 等
        return False, f"{url} -> {type(exc).__name__}: {exc}"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast(int, sock.getsockname()[1])


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """ waitress 是被 python 壳拉起的同进程，但 Windows 上仍按进程树杀干净。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, text=True, timeout=20,
        )
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def start_isolated_service(wait_seconds: int = 90) -> tuple[str, dict[str, str]]:
    """临时目录里起一台一次性服务：正式库的一致性快照 + 空闲端口 + 独立日志。

    走 HTTP 的门禁天然会往它们打到的那台库里留数据（含软删残骸）。默认打用户那台 8760
    就等于每跑一次门禁就往正式库写一批；隔离模式把写入面挪到用完即删的快照库上。
    密钥与提示词仍指正式路径——快照里的加密列要靠同一把钥匙才解得开。
    清理登记在 atexit，正常结束、异常退出都会收掉进程与临时目录。
    """
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_gates_"))
    db_path = tmp_dir / "aitp.db"
    sys.path.insert(0, str(SCRIPTS_DIR))
    from _db_snapshot import LIVE_DB, snapshot

    snapshot(LIVE_DB, db_path)
    port = _free_port()
    overrides = {
        "AITP_HOST": "127.0.0.1",
        "AITP_PORT": str(port),
        "AITP_DB_PATH": str(db_path),
        "AITP_LOG_PATH": str(tmp_dir / "aitp.log"),
    }
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "backend" / "run.py")],
        env={**os.environ, **overrides},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def _cleanup() -> None:
        _terminate(proc)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    atexit.register(_cleanup)
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + wait_seconds
    detail = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            _cleanup()
            raise RuntimeError(f"临时服务进程提前退出（exit={proc.returncode}）")
        up, detail = probe_health(base_url)
        if up:
            return base_url, {**overrides, "AITP_BASE_URL": base_url}
        time.sleep(1.0)
    _cleanup()
    raise RuntimeError(f"临时服务在 {wait_seconds}s 内没有就绪：{detail}")


def parse_counts(output: str) -> tuple[int | None, int | None]:
    """优先取最后一条 PASS=n FAIL=n 汇总行；否则统计 [PASS]/[FAIL] 标记行。"""
    matched = COUNT_RE.findall(output)
    if matched:
        passed, failed = matched[-1]
        return int(passed), int(failed)
    p = len(re.findall(r"^\[PASS\]", output, re.MULTILINE))
    f = len(re.findall(r"^\[FAIL\]", output, re.MULTILINE))
    if p or f:
        return p, f
    return None, None


def run_script(name: str, timeout: int, env: dict[str, str], verbose: bool) -> dict[str, Any]:
    path = SCRIPTS_DIR / f"{name}.py"
    started = datetime.datetime.now()
    record: dict[str, Any] = {"name": name, "file": str(path.relative_to(ROOT))}
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), env=env, timeout=timeout,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        record["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        output = stdout + stderr
        record["exit_code"] = -1
        record["status"] = f"TIMEOUT(>{timeout}s)"
    record["duration_s"] = round((datetime.datetime.now() - started).total_seconds(), 1)
    if verbose:
        print(output, flush=True)
    passed, failed = parse_counts(output)
    record["pass"] = passed
    record["fail"] = failed
    if "status" not in record:
        ok = record["exit_code"] == 0 and not (failed or 0)
        record["status"] = "PASS" if ok else "FAIL"
    tail = [line for line in output.splitlines() if line.strip()]
    record["output_tail"] = "\n".join(tail[-25:])
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="一键跑全部确定性门禁脚本")
    parser.add_argument("--only", default="", help="子串匹配，逗号分隔，只跑命中的脚本")
    parser.add_argument("--skip-name", action="append", default=[],
                        help="排除脚本名子串，可多次传入")
    parser.add_argument("--timeout", type=int, default=300, help="单脚本超时秒数，默认 300")
    parser.add_argument("--list", action="store_true", help="只列出将执行的脚本清单")
    parser.add_argument("--json", dest="json_path", default="", help="结果写入 JSON 文件路径")
    parser.add_argument("--base-url", default="", help="本地服务地址，默认取 AITP_BASE_URL")
    parser.add_argument("--verbose", action="store_true", help="打印每个脚本的完整输出")
    parser.add_argument("--isolated", action="store_true",
                        help="自起一台一次性临时服务（正式库快照 + 空闲端口 + 独立日志），"
                             "走 HTTP 的门禁改打它，不再往正式库写残骸")
    args = parser.parse_args()

    base_url = (args.base_url or os.environ.get("AITP_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    iso_env: dict[str, str] = {}
    if args.isolated and not args.list:
        if args.base_url:
            print("[FAIL] --isolated 与 --base-url 互斥：隔离模式自己决定端口。", flush=True)
            return 1
        base_url, iso_env = start_isolated_service()
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    skip = [s.strip() for s in args.skip_name if s.strip()]

    selected = apply_filters(discover(), only, skip)
    if not selected:
        print("[FAIL] 过滤后没有任何门禁脚本可跑", flush=True)
        return 1
    if args.list:
        print("将执行的门禁脚本（按字典序）：", flush=True)
        for n in selected:
            tag = " [需本地服务]" if n in SERVICE_DEPENDENT else ""
            print(f"  {n}{tag}", flush=True)
        print(f"（已永久排除：{', '.join(sorted(HARD_EXCLUDED))}——非确定性、调用真实模型）",
              flush=True)
        return 0

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["AITP_BASE_URL"] = base_url
    env.update(iso_env)

    service_up, health_detail = probe_health(base_url)
    if not service_up:
        need = [n for n in selected if n in SERVICE_DEPENDENT]
        print(f"[WARN] 本地服务不可用（{health_detail}）。", flush=True)
        if need:
            print(f"[WARN] 其中 {', '.join(need)} 需要已启动的本地服务，"
                  "将直接记为 FAIL（runner 不自动启动服务）。", flush=True)
        print("[WARN] 如需全部绿灯：先启动服务（python backend/run.py）再重跑。", flush=True)
    else:
        print(f"[ENV] 服务健康检查通过：{health_detail}", flush=True)
    print(f"[ENV] python={sys.executable} base_url={base_url} "
          f"timeout={args.timeout}s 共 {len(selected)} 个脚本", flush=True)
    if iso_env:
        print(f"[ENV] 隔离模式：门禁写入的是临时快照库 {iso_env['AITP_DB_PATH']}"
              "（跑完即删），正式库仅在取快照时以只读方式访问", flush=True)

    results: list[dict[str, Any]] = []
    for name in selected:
        if not service_up and name in SERVICE_DEPENDENT:
            print(f"== {name}: 跳过执行（需先启动本地服务）", flush=True)
            results.append({
                "name": name, "file": f"backend/scripts/{name}.py",
                "exit_code": None, "pass": None, "fail": None,
                "duration_s": 0.0, "status": "FAIL",
                "output_tail": f"需先启动本地服务：{health_detail}",
            })
            continue
        print(f"== 运行 {name} ...", flush=True)
        rec = run_script(name, args.timeout, env, args.verbose)
        p = "-" if rec["pass"] is None else rec["pass"]
        f = "-" if rec["fail"] is None else rec["fail"]
        print(f"== {name}: {rec['status']} exit={rec['exit_code']} PASS={p} FAIL={f}"
              f" ({rec['duration_s']}s)", flush=True)
        if rec["status"] != "PASS":
            print("--- 输出末尾（完整输出请加 --verbose）---", flush=True)
            print(rec["output_tail"], flush=True)
        results.append(rec)

    print()
    print(f"{'脚本':<32}{'退出码':>8}{'状态':>10}{'PASS':>8}{'FAIL':>8}{'耗时s':>9}")
    print("-" * 78)
    for r in results:
        ec = "-" if r["exit_code"] is None else r["exit_code"]
        p = "-" if r["pass"] is None else r["pass"]
        f = "-" if r["fail"] is None else r["fail"]
        print(f"{r['name']:<32}{ec:>8}{r['status']:>10}{p:>8}{f:>8}{r['duration_s']:>9}")

    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = len(results) - n_pass
    print(f"\n门禁脚本合计: PASS={n_pass} FAIL={n_fail}")
    if n_fail:
        print("失败脚本：" + ", ".join(r["name"] for r in results if r["status"] != "PASS"))

    if args.json_path:
        payload = {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "base_url": base_url, "service_up": service_up,
            "timeout": args.timeout, "results": results,
            "summary": {"pass": n_pass, "fail": n_fail},
        }
        json_path = pathlib.Path(args.json_path)
        if not json_path.is_absolute():
            json_path = ROOT / json_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"结果 JSON 已写入：{json_path}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
