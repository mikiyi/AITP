"""持久化轻量任务队列。

单用户本地工作台不需要分布式队列，但需要三件事：
  1. 排队 + 并发上限，批量作业不会同时把 CPU / 子进程打满；
  2. 状态落库，重启后排队任务继续、运行中任务标记中断；
  3. 可取消、可重试，前端任务中心能看见每条任务卡在哪。

任务执行在独立线程 + 独立 SQLite 连接里完成，不复用请求上下文。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, cast

from ..common.time_utils import utcnow_iso
from ..db.connection import get_standalone_conn
from ..errors import AppError, ErrorCode
from ..repositories.base import loads
from ..repositories.sys_config_repo import SysConfigRepository
from . import task_control

TASK_QUEUED = "queued"
TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_STATUSES: tuple[str, ...] = (
    TASK_QUEUED, TASK_RUNNING, TASK_SUCCESS, TASK_FAILED, TASK_CANCELLED,
)

STATUS_LABELS = {
    TASK_QUEUED: "排队中",
    TASK_RUNNING: "执行中",
    TASK_SUCCESS: "成功",
    TASK_FAILED: "失败",
    TASK_CANCELLED: "已取消",
}

KIND_LABELS: dict[str, str] = {
    "automation.run": "自动化脚本执行",
    "interface.batch_run": "接口用例批量执行",
    "knowledge.organize": "知识库自动整理",
}

#: 并发上限配置键；默认 2，单用户本机够用又不至于互相拖死
CONFIG_CONCURRENCY = "task.queue.concurrency"
DEFAULT_CONCURRENCY = 2
MAX_CONCURRENCY = 8
#: 空闲时的轮询间隔（秒）
POLL_INTERVAL = 0.8
#: 连续连接失败多少次后线程退出（临时库被删时避免空转）
MAX_DB_FAILURES = 5

Handler = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any], Path], dict[str, Any]]

_HANDLERS: dict[str, Handler] = {}
_workers: list[threading.Thread] = []
_state: dict[str, Any] = {"db_path": None, "app": None}
_lock = threading.Lock()


class TaskCancelled(Exception):
    """任务在执行过程中被取消。"""


def register(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def set_app(app: Any) -> None:
    """保存应用实例；worker 线程执行处理器前需要压入 app context。"""
    _state["app"] = app


def _run_handler(handler: Handler, conn: sqlite3.Connection, task: dict[str, Any],
                 payload: dict[str, Any], db_path: Path) -> dict[str, Any]:
    app = _state.get("app")
    if app is None:
        return handler(conn, task, payload, db_path)
    # 处理器可能读 current_app.config（密钥、提示词目录、外发策略），线程里没有请求上下文
    with app.app_context():
        return handler(conn, task, payload, db_path)


def _db_path_of(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    if row is None or not row[2]:
        raise RuntimeError("无法确定数据库路径")
    return Path(str(row[2]))


def _concurrency(db_path: Path) -> int:
    conn = get_standalone_conn(db_path)
    try:
        raw = SysConfigRepository(conn).get_value(CONFIG_CONCURRENCY, str(DEFAULT_CONCURRENCY))
    finally:
        conn.close()
    try:
        value = int(str(raw or ""))
    except (TypeError, ValueError):
        return DEFAULT_CONCURRENCY
    return max(1, min(MAX_CONCURRENCY, value))


def submit(
    conn: sqlite3.Connection,
    *,
    kind: str,
    title: str,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int = 1,
) -> dict[str, Any]:
    """写入一条排队任务并确保 worker 已启动，返回任务快照。"""
    if kind not in _HANDLERS:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知任务类型：{kind}")
    now = utcnow_iso()
    cur = conn.execute(
        """
        INSERT INTO sys_task
            (kind, title, status, priority, payload_json, attempts, max_attempts,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            kind, title[:200], TASK_QUEUED, priority,
            json.dumps(payload or {}, ensure_ascii=False), max(1, max_attempts), now, now,
        ),
    )
    task_id = int(cur.lastrowid or 0)
    conn.commit()
    ensure_workers(_db_path_of(conn))
    return get_task(conn, task_id) or {}


def ensure_workers(db_path: Path) -> None:
    """按配置补齐 worker 线程；已有线程不再重复启动。"""
    desired = _concurrency(db_path)
    with _lock:
        _state["db_path"] = db_path
        alive = [thread for thread in _workers if thread.is_alive()]
        _workers[:] = alive
        for index in range(len(alive), desired):
            thread = threading.Thread(
                target=_worker_loop, name=f"aitp-task-{index + 1}", daemon=True
            )
            _workers.append(thread)
            thread.start()


def start_workers_if_pending(db_path: Path) -> None:
    """启动时只有存在排队任务才起线程，避免空库也常驻轮询。"""
    conn = get_standalone_conn(db_path)
    try:
        pending = conn.execute(
            "SELECT COUNT(*) FROM sys_task WHERE status = ?", (TASK_QUEUED,)
        ).fetchone()[0]
    finally:
        conn.close()
    if int(pending) > 0:
        ensure_workers(db_path)


def _worker_loop() -> None:
    failures = 0
    while True:
        with _lock:
            db_path = cast(Path | None, _state.get("db_path"))
        if db_path is None:
            return
        try:
            task = _claim(db_path)
            failures = 0
        except sqlite3.Error:
            failures += 1
            if failures >= MAX_DB_FAILURES:
                return
            time.sleep(2)
            continue
        if task is None:
            time.sleep(POLL_INTERVAL)
            continue
        _execute(db_path, task)


def _claim(db_path: Path) -> dict[str, Any] | None:
    conn = get_standalone_conn(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM sys_task WHERE status = ? ORDER BY priority ASC, id ASC LIMIT 1",
            (TASK_QUEUED,),
        ).fetchone()
        if row is None:
            return None
        now = utcnow_iso()
        cur = conn.execute(
            """
            UPDATE sys_task
            SET status = ?, started_at = COALESCE(started_at, ?), attempts = attempts + 1,
                updated_at = ?
            WHERE id = ? AND status = ?
            """,
            (TASK_RUNNING, now, now, int(row["id"]), TASK_QUEUED),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return None
        conn.commit()
        task = conn.execute("SELECT * FROM sys_task WHERE id = ?", (int(row["id"]),)).fetchone()
        return dict(task) if task else None
    finally:
        conn.close()


def _execute(db_path: Path, task: dict[str, Any]) -> None:
    task_id = int(task["id"])
    key = f"task:{task_id}"
    task_control.register(key)
    conn = get_standalone_conn(db_path)
    try:
        payload = loads(task.get("payload_json"), {}) or {}
        handler = _HANDLERS.get(str(task.get("kind") or ""))
        if handler is None:
            raise RuntimeError(f"任务类型未注册：{task.get('kind')}")
        if int(task.get("cancel_requested") or 0) or task_control.is_cancelled(key):
            _finish(conn, task, TASK_CANCELLED, {}, "")
            return
        result = _run_handler(handler, conn, task, payload, db_path)
        conn.commit()
        if task_control.is_cancelled(key):
            _finish(conn, task, TASK_CANCELLED, result, "用户取消")
        else:
            _finish(conn, task, TASK_SUCCESS, result, "")
    except TaskCancelled:
        conn.rollback()
        _finish(conn, task, TASK_CANCELLED, {}, "用户取消")
    except AppError as exc:
        conn.rollback()
        _finish_failure(conn, task, str(exc))
    except Exception as exc:  # noqa: BLE001 - 任务失败要落库，不能吞掉
        conn.rollback()
        _finish_failure(conn, task, f"{type(exc).__name__}: {exc}")
    finally:
        task_control.release(key)
        conn.close()


def _finish_failure(conn: sqlite3.Connection, task: dict[str, Any], message: str) -> None:
    attempts = int(task.get("attempts") or 0)
    max_attempts = int(task.get("max_attempts") or 1)
    if attempts < max_attempts:
        # 还有重试机会：回到队列，保留已累计的尝试次数
        conn.execute(
            "UPDATE sys_task SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
            (TASK_QUEUED, message[:2000], utcnow_iso(), int(task["id"])),
        )
        conn.commit()
        return
    _finish(conn, task, TASK_FAILED, {}, message)


def _finish(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    status: str,
    result: dict[str, Any],
    error_message: str,
) -> None:
    now = utcnow_iso()
    finished = now if status in (TASK_SUCCESS, TASK_FAILED, TASK_CANCELLED) else None
    conn.execute(
        """
        UPDATE sys_task
        SET status = ?, result_json = ?, error_message = ?, progress = ?,
            finished_at = COALESCE(?, finished_at), updated_at = ?
        WHERE id = ?
        """,
        (
            status, json.dumps(result or {}, ensure_ascii=False), error_message[:2000],
            100 if status == TASK_SUCCESS else int(task.get("progress") or 0),
            finished, now, int(task["id"]),
        ),
    )
    conn.commit()


def task_summary(row: dict[str, Any]) -> dict[str, Any]:
    kind = str(row.get("kind") or "")
    return {
        "id": row.get("id"),
        "kind": kind,
        "kindLabel": KIND_LABELS.get(kind, kind),
        "title": row.get("title"),
        "status": row.get("status"),
        "statusLabel": STATUS_LABELS.get(str(row.get("status")), str(row.get("status") or "")),
        "priority": row.get("priority"),
        "attempts": row.get("attempts"),
        "maxAttempts": row.get("max_attempts"),
        "progress": row.get("progress"),
        "cancelRequested": bool(row.get("cancel_requested")),
        "payload": loads(row.get("payload_json"), {}) or {},
        "result": loads(row.get("result_json"), {}) or {},
        "errorMessage": row.get("error_message") or "",
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def get_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM sys_task WHERE id = ?", (task_id,)).fetchone()
    return task_summary(dict(row)) if row else None


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    kind: str = "",
    keyword: str = "",
    page_num: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    where = ["1 = 1"]
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if keyword:
        where.append("(title LIKE ? OR error_message LIKE ?)")
        needle = f"%{keyword}%"
        params.extend([needle, needle])
    clause = " AND ".join(where)
    total = int(conn.execute(f"SELECT COUNT(*) FROM sys_task WHERE {clause}", params).fetchone()[0])
    offset = max(0, (page_num - 1) * page_size)
    rows = conn.execute(
        f"SELECT * FROM sys_task WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?",
        [*params, page_size, offset],
    ).fetchall()
    return {
        "list": [task_summary(dict(row)) for row in rows],
        "total": total,
        "pageNum": page_num,
        "pageSize": page_size,
    }


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {status: 0 for status in TASK_STATUSES}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS c FROM sys_task GROUP BY status"
    ).fetchall():
        key = str(row["status"])
        if key in counts:
            counts[key] = int(row["c"])
    counts["total"] = sum(counts.values())
    return counts


def cancel_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sys_task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    task = dict(row)
    status = str(task.get("status") or "")
    if status in (TASK_SUCCESS, TASK_FAILED, TASK_CANCELLED):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "任务已经结束，无需取消")
    now = utcnow_iso()
    if status == TASK_QUEUED:
        conn.execute(
            "UPDATE sys_task SET status = ?, finished_at = ?, error_message = ?, updated_at = ?"
            " WHERE id = ? AND status = ?",
            (TASK_CANCELLED, now, "用户取消", now, task_id, TASK_QUEUED),
        )
        conn.commit()
        return get_task(conn, task_id) or {}
    # 运行中：先打标记并唤醒可取消的执行器，进度由执行方在检查点收口
    conn.execute(
        "UPDATE sys_task SET cancel_requested = 1, updated_at = ? WHERE id = ?",
        (now, task_id),
    )
    conn.commit()
    task_control.cancel(f"task:{task_id}")
    result = get_task(conn, task_id) or {}
    result["cancelRequested"] = True
    return result


def retry_task(conn: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM sys_task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    status = str(row["status"] or "")
    if status not in (TASK_FAILED, TASK_CANCELLED):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有失败或已取消的任务可以重试")
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE sys_task
        SET status = ?, attempts = 0, error_message = '', result_json = '{}',
            progress = 0, cancel_requested = 0, started_at = NULL, finished_at = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (TASK_QUEUED, now, task_id),
    )
    conn.commit()
    ensure_workers(_db_path_of(conn))
    return get_task(conn, task_id) or {}
