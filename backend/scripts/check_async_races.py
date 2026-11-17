"""验证异步调度竞态：矩阵并发双跑只允许一个成功，任务取消不再假成功（B3）。

    python backend/scripts/check_async_races.py

临时库 + 两个独立 Flask 请求并发调同一矩阵；取消用服务层直接验证
queued→running 竞态后的真实状态处理。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import job_queue  # noqa: E402
from flask import Flask  # noqa: E402

V1 = "/api/v1"
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "races.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8778, debug=False, db_path=db_path,
        log_path=workdir / "races.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def create_project(app: Flask) -> int:
    client = app.test_client()
    body = client.post(f"{V1}/projects", json={"code": "RACE", "name": "竞态项目"}).get_json()
    return int(body["data"]["id"])


def create_env(app: Flask) -> int:
    client = app.test_client()
    body = client.post(f"{V1}/test-envs", json={
        "name": "竞态环境", "kind": "test",
        "app": {"baseUrl": "http://127.0.0.1:1", "servers": []},
        "db": {"type": "sqlite", "host": ""},
    }).get_json()
    return int(body["data"]["id"])


def insert_matrix(db_path: pathlib.Path, project_id: int, env_id: int, no: str) -> int:
    conn = sqlite3.connect(db_path)
    now = utcnow_iso()
    matrix_id = int(conn.execute(
        "INSERT INTO biz_execution_matrix"
        " (project_id, matrix_no, name, mode, status, env_ids_json, script_ids_json,"
        "  target_mode, targets_json, cell_total, cell_done, summary_json, created_at, updated_at)"
        " VALUES (?, ?, '竞态矩阵', 'interface', 'draft', ?, '[]', 'default', '[]',"
        "         0, 0, '{}', ?, ?)",
        (project_id, no, json.dumps([env_id]), now, now),
    ).lastrowid or 0)
    conn.commit()
    conn.close()
    return matrix_id


def concurrent_run(app: Flask, project_id: int, matrix_id: int) -> list[int]:
    results: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def worker() -> None:
        client = app.test_client()
        barrier.wait()
        body = client.post(
            f"{V1}/execution-matrices/{matrix_id}/run?projectId={project_id}",
            json={"confirmProd": False},
        ).get_json()
        with lock:
            results.append(int(body.get("code") or -1))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    return results


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-races-"))
    db_path = workdir / "races.db"
    try:
        app = build_app(workdir)
        project_id = create_project(app)
        env_id = create_env(app)

        # ---- 矩阵并发调度：一个成功，一个被拒绝 ----
        matrix_id = insert_matrix(db_path, project_id, env_id, "MX-RACE-1")
        codes = concurrent_run(app, project_id, matrix_id)
        check("并发调度只投递一次",
              sorted(codes) == sorted([ErrorCode.SUCCESS, ErrorCode.ILLEGAL_OPERATION]),
              str(codes))
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        matrix = conn.execute("SELECT status FROM biz_execution_matrix WHERE id = ?",
                              (matrix_id,)).fetchone()
        check("矩阵状态进入排队", str(matrix["status"]) in ("queued", "running", "done"),
              str(dict(matrix) if matrix else None))
        tasks = int(conn.execute("SELECT COUNT(*) FROM sys_task WHERE kind = 'matrix.run'")
                    .fetchone()[0])
        check("只产生一个矩阵任务", tasks == 1, str(tasks))

        # ---- 取消：queued→running 竞态后按运行中处理 ----
        now = utcnow_iso()
        conn.execute(
            "INSERT INTO sys_task (kind, title, status, priority, payload_json, result_json,"
            " error_message, attempts, max_attempts, progress, cancel_requested,"
            " created_at, updated_at)"
            " VALUES ('check.race', '竞态运行任务', 'running', 100, '{}', '{}', '', 1, 1, 0, 0, ?, ?)",
            (now, now),
        )
        running_id = int(conn.execute("SELECT MAX(id) FROM sys_task").fetchone()[0])
        conn.commit()
        running = job_queue.cancel_task(conn, running_id)
        check("运行中任务取消只打标记",
              running.get("cancelRequested") is True and running.get("status") == "running",
              str(running)[:200])

        # ---- 取消：已经结束的任务必须拒绝 ----
        conn.execute(
            "INSERT INTO sys_task (kind, title, status, priority, payload_json, result_json,"
            " error_message, attempts, max_attempts, progress, cancel_requested,"
            " created_at, updated_at)"
            " VALUES ('check.race', '已结束任务', 'failed', 100, '{}', '{}', 'boom', 1, 1, 0, 0, ?, ?)",
            (now, now),
        )
        failed_id = int(conn.execute("SELECT MAX(id) FROM sys_task").fetchone()[0])
        conn.commit()
        try:
            job_queue.cancel_task(conn, failed_id)
            check("已结束任务取消被拒绝", False, "未抛错")
        except AppError as exc:
            check("已结束任务取消被拒绝", exc.code == ErrorCode.ILLEGAL_OPERATION, exc.message)

        # ---- 取消：正常排队任务即时取消 ----
        conn.execute(
            "INSERT INTO sys_task (kind, title, status, priority, payload_json, result_json,"
            " error_message, attempts, max_attempts, progress, cancel_requested,"
            " created_at, updated_at)"
            " VALUES ('check.race', '排队任务', 'queued', 100, '{}', '{}', '', 0, 1, 0, 0, ?, ?)",
            (now, now),
        )
        queued_id = int(conn.execute("SELECT MAX(id) FROM sys_task").fetchone()[0])
        cancelled = job_queue.cancel_task(conn, queued_id)
        check("排队任务取消成功", cancelled.get("status") == "cancelled", str(cancelled)[:200])
        conn.close()

        # 清理并发测试投递的任务，避免 worker 在临时库反复执行
        cleanup = sqlite3.connect(db_path)
        cleanup.execute("UPDATE sys_task SET status = 'cancelled' WHERE status IN ('queued', 'running')")
        cleanup.commit()
        cleanup.close()
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
