"""SQLite 并发写与异常治理门禁（GAP-26 / C2 批次）。

判据来自 docs/AITP-二期工程清单.md §5.5 的 C2 实施规格，全部自己取证，不信自述：

  1. 并发写零失败：16 并发 × N 次建用例 / 缺陷 / 需求 / 待办，一条都不许失败，
     并且「接口报成功的条数 == 库里实际多出的条数」；
     编号既不重复也不丢号（软删复用型取号必须是连续的空缺号）；
  2. 一条 database is locked 都不许出现，busy 重试与写锁超时计数归零，
     口径来自 /api/v1/health 的 dbWrite 段（db/write_guard.write_stats()）；
  3. 长任务不阻塞请求线程：后台导出任务跑着的时候，普通读请求照样要有正常响应
     （run-all 是否任务化单独如实上报，不伪造通过）；
  4. 取号站点必须真的做了串行化（AST 扫 repositories 里所有 next_* 型取号函数）；
  5. 异常治理：`except Exception` 之后裸 `pass` 的站点必须清零
     （只放行 logging 自身——日志机器不能靠日志报错）。

用临时库 + 独立端口的自建服务跑，不碰 data/aitp.db、不动 8760 的本地服务、不调模型。

    python backend/scripts/check_db_concurrency.py
    python backend/scripts/check_db_concurrency.py --writes 240 --workers 24

退出码：FAIL=0 为 0，任何 FAIL 为 1（TODO 计数只作如实汇报，不影响退出码）。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]

_passed = 0
_failed = 0
_todos: list[str] = []

#: 允许保留裸 pass 的文件：日志机器自身出错时不能再靠日志报错
BARE_PASS_ALLOWLIST = {"logging_setup.py"}

SUCCESS_CODE = 200000

JsonDict = dict[str, Any]


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        return
    _failed += 1
    print(f"[FAIL] {name}  {detail}", flush=True)


def info(message: str) -> None:
    print(f"[DATA] {message}", flush=True)


def todo(name: str, detail: str) -> None:
    """已知未完成的判据：如实打印并单独计数，绝不伪装成 PASS。"""
    _todos.append(name)
    print(f"[TODO] {name}  {detail}", flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(cast(tuple[str, int], probe.getsockname())[1])


def http_json(method: str, url: str, payload: JsonDict | None = None,
              timeout: float = 60.0) -> tuple[int, JsonDict, float]:
    """返回 (业务码, 包封, 耗时毫秒)；HTTP 层异常也算一次失败，不抛出去打断并发批次。"""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")[:200]
        return -1, {"code": exc.code, "message": f"http{exc.code}:{raw}"}, \
            (time.perf_counter() - started) * 1000
    except Exception as exc:  # noqa: BLE001 - 工装要把连接层异常也统计成一次失败
        return 0, {"code": 0, "message": f"{type(exc).__name__}: {exc}"}, \
            (time.perf_counter() - started) * 1000
    elapsed = (time.perf_counter() - started) * 1000
    if not isinstance(body, dict):
        return 0, {"code": 0, "message": "非对象包封"}, elapsed
    return int(body.get("code") or 0), cast(JsonDict, body), elapsed


# ---------------- 被测服务 ----------------

class Server:
    """临时库 + 独立端口的自建服务；只关自己起的进程，不碰已跑着的服务。"""

    def __init__(self, workdir: pathlib.Path, db_path: pathlib.Path) -> None:
        self.workdir = workdir
        self.db_path = db_path
        self.port = free_port()
        self.log_path = workdir / "server.log"
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        env = dict(os.environ)
        env.update({
            "AITP_DB_PATH": str(self.db_path),
            "AITP_PORT": str(self.port),
            "AITP_LOG_PATH": str(self.workdir / "aitp.log"),
            "AITP_DEPLOY_LOG_PATH": str(self.workdir / "deploy.log"),
            "AITP_SECRET_KEY_PATH": str(self.workdir / ".secret_key"),
            "PYTHONIOENCODING": "utf-8",
        })
        handle = self.log_path.open("wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(ROOT / "backend" / "run.py")],
            cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT)
        self._handle = handle
        base = f"http://127.0.0.1:{self.port}"
        for _ in range(90):
            code, _, _ = http_json("GET", f"{base}/api/v1/health", timeout=3.0)
            if code == SUCCESS_CODE:
                info(f"临时服务已起：{base}（库={self.db_path}）")
                return
            time.sleep(1.0)
        self.stop()
        raise SystemExit(f"[FAIL] 临时服务未就绪，看 {self.log_path}")

    def stop(self) -> None:
        if self.proc is not None:
            self.proc.kill()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                pass
            self._handle.close()
            self.proc = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return self.base + path

    def app_log(self) -> str:
        path = self.workdir / "aitp.log"
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


# ---------------- 判据 1+2：并发写 ----------------

def race_create(server: Server, path: str, payloads: list[JsonDict],
                workers: int) -> list[tuple[int, str]]:
    """并发打创建接口，返回 (业务码, 消息) 清单；不关心内容，只关心成没成。"""
    url = server.base + path

    def one(payload: JsonDict) -> tuple[int, str]:
        code, body, _ = http_json("POST", url, payload, timeout=120.0)
        return code, str(body.get("message") or "")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, payloads))


def sequence_of(nos: list[str], prefix: str) -> list[int]:
    values: list[int] = []
    for text in nos:
        head, _, tail = text.rpartition("-")
        if head.upper() != prefix.upper() or not tail.isdigit():
            continue
        values.append(int(tail))
    return sorted(values)


def check_numbering(db_path: pathlib.Path, table: str, column: str, project_id: int,
                    prefix: str, label: str, expected: int) -> None:
    """编号既不重复也不丢号：库里条数 = 成功数，序号正好是 1..N 连续一段。"""
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    try:
        rows = conn.execute(
            f"SELECT {column} FROM {table} WHERE project_id = ? AND is_deleted = 0",
            (project_id,)).fetchall()
    finally:
        conn.close()
    nos = [str(row[0] or "") for row in rows]
    seq = sequence_of(nos, prefix)
    check(f"{label} 落库条数与成功数一致", len(nos) == expected,
          f"库里 {len(nos)} 条 / 接口成功 {expected} 条")
    check(f"{label} 编号不重复", len(set(nos)) == len(nos),
          f"去重后 {len(set(nos))} 个 / 共 {len(nos)} 条")
    check(f"{label} 编号不丢号", seq == list(range(1, expected + 1)),
          f"实际序号段={seq[:3]}…{seq[-3:] if seq else ''} / 期望 1..{expected}")


def concurrent_read_probe(server: Project, path: str, rounds: int) -> tuple[float, float, int]:
    """并发写期间打普通读请求：返回 (p50ms, p95ms, 失败数)，用来验「写没把读饿死」。"""
    latencies: list[float] = []
    failures = 0
    for _ in range(rounds):
        code, _, elapsed = http_json("GET", path, timeout=30.0)
        latencies.append(elapsed)
        if code != SUCCESS_CODE:
            failures += 1
    latencies.sort()
    if not latencies:
        return 0.0, 0.0, failures
    return latencies[len(latencies) // 2], latencies[min(len(latencies) - 1,
                                                         int(len(latencies) * 0.95))], failures


class Project:
    """把 base 与项目号打包，省掉到处拼字符串。"""

    def __init__(self, base: str, project_id: int) -> None:
        self.base = base
        self.project_id = project_id

    def url(self, path: str) -> str:
        return self.base + path


def write_stats_snapshot(server: Server) -> JsonDict:
    _, body, _ = http_json("GET", server.url("/api/v1/health"), timeout=10.0)
    data = body.get("data")
    if not isinstance(data, dict):
        return {}
    stats = cast(JsonDict, data).get("dbWrite")
    return cast(JsonDict, stats) if isinstance(stats, dict) else {}


def wait_task_terminal(project: Project, task_id: int, timeout: float = 120.0) -> str:
    """轮询一条队列任务直到终态，返回最后一次观察到的状态。"""
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        _, snap, _ = http_json("GET", f"{project.base}/api/v1/tasks/{task_id}", timeout=10.0)
        payload = snap.get("data")
        detail = cast(JsonDict, payload) if isinstance(payload, dict) else {}
        status = str(detail.get("status") or "")
        if status in ("success", "failed", "cancelled"):
            return status
        time.sleep(0.5)
    return status


# ---------------- 判据 3：长任务 ----------------

def check_long_task(project: Project, project_id: int) -> None:
    """后台任务在跑时普通请求必须照常返回；run-all 的任务化状态如实汇报。"""
    code, body, _ = http_json("POST", project.url("/api/v1/validation/run-all"),
                              {"projectId": project_id}, timeout=60.0)
    data = body.get("data")
    task_id = cast(JsonDict, data).get("taskId") if isinstance(data, dict) else None
    if code == SUCCESS_CODE and isinstance(task_id, int):
        check("长任务已任务化：validation/run-all 只回 taskId 不占请求线程", task_id > 0,
              f"taskId={task_id}")
        status = wait_task_terminal(project, task_id)
        check("全量验证任务在队列里跑到成功", status == "success",
              f"taskId={task_id} status={status}")
    else:
        check("长任务已任务化：validation/run-all 只回 taskId 不占请求线程", False,
              f"code={code} 返回体={str(data)[:120]}")
        todo("长任务未任务化：POST /api/v1/validation/run-all 仍在请求线程同步执行",
             f"code={code} 返回体={str(data)[:120]}（GAP-26 §4 未完成项，不计入门禁绿灯）")

    code, submit, _ = http_json(
        "POST", project.url("/api/v1/test-cases/export/async"),
        {"format": "csv", "query": {"projectId": project_id}}, timeout=30.0)
    task = submit.get("data")
    if code != SUCCESS_CODE or not isinstance(task, dict):
        check("后台导出任务可提交", False, f"code={code} {str(submit)[:160]}")
        return
    task_id = int(cast(JsonDict, task).get("id") or 0)
    check("后台导出任务可提交", task_id > 0, f"快照={str(task)[:160]}")

    # 任务还在队列里跑的时候，同时打并发读，看请求线程有没有被后台任务拖住
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(concurrent_read_probe, project,
                              f"{project.base}/api/v1/test-cases?pageSize=20", 12)
                   for _ in range(4)]
        probes = [f.result() for f in futures]

    status = ""
    detail: JsonDict = {}
    for _ in range(120):
        _, snap, _ = http_json("GET", f"{project.base}/api/v1/tasks/{task_id}", timeout=10.0)
        payload = snap.get("data")
        detail = cast(JsonDict, payload) if isinstance(payload, dict) else {}
        status = str(detail.get("status") or "")
        if status in ("success", "failed", "cancelled"):
            break
        time.sleep(0.5)
    check("后台导出任务不受并发写影响跑完", status == "success",
          f"taskId={task_id} status={status} {str(detail)[:200]}")

    p95 = max(item[1] for item in probes)
    failed_reads = sum(item[2] for item in probes)
    info(f"任务在跑期间的并发读：p95={p95:.0f}ms 失败={failed_reads}")
    check("后台任务不阻塞请求线程", failed_reads == 0 and p95 < 5000.0,
          f"读失败={failed_reads} p95={p95:.0f}ms（阈值 5000ms）")


# ---------------- 判据 4：取号站点静态核对 ----------------

def claim_guarded_nodes() -> list[str]:
    """所有 next_* 型取号函数必须真的做了串行化（直接 begin_claim，或委托给已加固的入口）。"""
    guarded_calls = ("begin_claim", "next_no", "next_case_no")
    suspicious: list[str] = []
    for path in sorted((ROOT / "backend" / "aitp" / "repositories").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("next_"):
                continue
            if not (node.name.endswith("_no") or node.name.endswith("_seq")
                    or node.name.endswith("_version")):
                continue
            called: set[str] = set()
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                func = inner.func
                if isinstance(func, ast.Attribute):
                    called.add(func.attr)
                elif isinstance(func, ast.Name):
                    called.add(func.id)
            if not any(name in called for name in guarded_calls):
                suspicious.append(f"{path.name}:{node.lineno} {node.name}")
    return suspicious


def bare_pass_sites() -> list[str]:
    """`except Exception:` 之后什么也没做的站点（日志自身除外，全部要留一行记录）。"""
    found: list[str] = []
    for path in sorted((ROOT / "backend" / "aitp").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            exc = node.type
            if not (isinstance(exc, ast.Name) and exc.id in ("Exception", "BaseException")):
                continue
            statements = [
                item for item in node.body
                if not (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant))
            ]
            if len(statements) == 1 and isinstance(statements[0], ast.Pass):
                if path.name in BARE_PASS_ALLOWLIST:
                    continue
                found.append(f"{path.relative_to(ROOT / 'backend' / 'aitp')}:{node.lineno}")
    return found


# ---------------- 主流程 ----------------

def prepare(server: Server) -> int:
    code, body, _ = http_json("POST", server.url("/api/v1/projects"),
                              {"code": "CCY", "name": "并发门禁项目"}, timeout=30.0)
    data = body.get("data")
    if code != SUCCESS_CODE or not isinstance(data, dict) or not data.get("id"):
        raise SystemExit(f"[FAIL] 临时库建项目失败：code={code} {str(body)[:200]}")
    return int(cast(JsonDict, data)["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description="GAP-26 并发写与异常治理门禁")
    parser.add_argument("--writes", type=int, default=120, help="用例并发创建次数，默认 120")
    parser.add_argument("--workers", type=int, default=16, help="并发线程数，默认 16")
    args = parser.parse_args()

    workdir = ROOT / "logs" / "gate_db_concurrency"
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / "concurrency.db"
    for stale in sorted(workdir.glob("concurrency.db*")):
        stale.unlink(missing_ok=True)

    sys.path.insert(0, str(ROOT / "backend"))
    from aitp.db.migrator import init_db  # noqa: E402

    init_db(db_path)
    stamp = int(time.time())
    server = Server(workdir, db_path)
    server.start()
    try:
        project_id = prepare(server)
        project = Project(server.base, project_id)
        before = write_stats_snapshot(server)
        info(f"起跑前写锁计数：{before}")

        case_payloads = [{"projectId": project_id, "title": f"CCY-C{stamp}-{i:05d}"}
                         for i in range(args.writes)]
        bug_payloads = [{"projectId": project_id, "title": f"CCY-B{stamp}-{i:05d}"}
                        for i in range(60)]
        req_payloads = [{"projectId": project_id, "title": f"CCY-R{stamp}-{i:05d}"}
                        for i in range(60)]
        todo_payloads = [{"projectId": project_id, "title": f"CCY-T{stamp}-{i:05d}"}
                         for i in range(40)]

        started = time.perf_counter()
        results: dict[str, list[tuple[int, str]]] = {}
        with ThreadPoolExecutor(max_workers=4) as stages:
            stage_handles = {
                "用例": stages.submit(race_create, server, "/api/v1/test-cases",
                                    case_payloads, args.workers),
                "缺陷": stages.submit(race_create, server, "/api/v1/bugs",
                                    bug_payloads, 8),
                "需求": stages.submit(race_create, server, "/api/v1/requirements",
                                    req_payloads, 8),
                "待办": stages.submit(race_create, server, "/api/v1/todos",
                                    todo_payloads, 8),
            }
            for label, handle in stage_handles.items():
                results[label] = handle.result()
        elapsed = time.perf_counter() - started

        expected_counts = {"用例": args.writes, "缺陷": 60, "需求": 60, "待办": 40}
        all_failures: list[tuple[int, str]] = []
        for label, rows in results.items():
            ok_count = sum(1 for code, _ in rows if code == SUCCESS_CODE)
            bad = [(code, message) for code, message in rows if code != SUCCESS_CODE]
            all_failures.extend(bad)
            sample: dict[str, int] = {}
            for code, message in bad:
                key = f"code={code} {message[:80]}"
                sample[key] = sample.get(key, 0) + 1
            info(f"并发建{label}：{len(rows)} 次（成功 {ok_count} / 失败 {len(bad)}）"
                 + ("".join(f" | x{count} {key}" for key, count in
                            sorted(sample.items(), key=lambda kv: -kv[1])[:3]) if bad else ""))
            check(f"并发建{label}零失败", not bad, f"失败 {len(bad)} 条")
            check(f"{label}成功条数与落库一致", ok_count == expected_counts[label],
                  f"接口成功 {ok_count} / 应成功 {expected_counts[label]}")

        check("并发写不出现 database is locked",
              "database is locked" not in server.app_log().lower(),
              "服务日志里出现了 database is locked")
        check("并发写不出现编号唯一键冲突",
              "UNIQUE constraint failed" not in server.app_log(),
              "服务日志里出现了 UNIQUE constraint failed")

        check_numbering(db_path, "biz_test_case", "case_no", project_id, "FT", "用例编号",
                        args.writes)
        check_numbering(db_path, "biz_bug", "bug_no", project_id, "BUG", "缺陷编号", 60)
        check_numbering(db_path, "biz_requirement", "req_no", project_id, "REQ", "需求编号", 60)

        after = write_stats_snapshot(server)
        info(f"并发批次耗时 {elapsed:.1f}s；结束后写锁计数：{after}")
        check("write_stats() 可被外部读到", bool(after),
              "健康检查里没有 dbWrite 段（write_guard 没接线）")
        check("写锁提交计数在涨", int(after.get("commits") or 0) > int(before.get("commits") or 0),
              f"before={before.get('commits')} after={after.get('commits')}")
        check("写锁等待超时代价为零", int(after.get("timeouts") or 0) == 0,
              f"timeouts={after.get('timeouts')}")
        check("busy 重试错误价为零", int(after.get("busyErrors") or 0) == 0,
              f"busyErrors={after.get('busyErrors')}")
        info(f"写锁排队：waits={after.get('waits')} slowWaits={after.get('slowWaits')}"
             f" maxWaitMs={after.get('maxWaitMs')}（进程内写锁串行化生效则为非零等待，不是失败）")

        check_long_task(project, project_id)

        suspicious = claim_guarded_nodes()
        check("所有 next_* 取号站点都已做写事务串行化", not suspicious,
              "未调 begin_claim/next_no 的取号函数：" + "; ".join(suspicious))
        silent = bare_pass_sites()
        check("except Exception 后裸 pass 已清零", not silent,
              "仍静默吞掉的站点：" + "; ".join(silent))
    finally:
        server.stop()
        info(f"临时目录保留供复核：{workdir}")

    if _todos:
        print(f"[TODO] 本批未完成判据 {len(_todos)} 条：{'; '.join(_todos)}", flush=True)
    print(f"PASS={_passed} FAIL={_failed} TODO={len(_todos)}", flush=True)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
