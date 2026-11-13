"""Web 探索性测试的队列处理器（GAP-45）。

一次会话投递一个任务，任务内逐步真实驱动 MCP 浏览器并更新进度；
取消时把会话收口为「已取消」，失败时收口为「失败」并保留错误信息。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from . import audit_service, exploratory_service, job_queue, task_control


def run_exploration(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    session_id = int(payload.get("sessionId") or 0)
    task_id = int(task["id"])
    key = f"task:{task_id}"

    def on_progress(done: int, total: int) -> None:
        if task_control.is_cancelled(key):
            raise job_queue.TaskCancelled()
        progress = int(done * 100 / total) if total else 0
        conn.execute(
            "UPDATE sys_task SET progress = ?, updated_at = ? WHERE id = ?",
            (progress, utcnow_iso(), task_id),
        )
        conn.commit()

    try:
        summary = exploratory_service.execute_session(
            conn, session_id, db_path,
            on_progress=on_progress,
            cancel_check=lambda: task_control.is_cancelled(key),
        )
    except job_queue.TaskCancelled:
        exploratory_service.mark_cancelled(conn, session_id)
        raise
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        "exploration_session",
        session_id,
        "Web 探索性测试",
        {
            "taskId": task_id,
            "steps": summary.get("steps"),
            "anomalies": summary.get("anomalies"),
            "drafts": summary.get("drafts"),
            "degraded": summary.get("degraded"),
        },
        result="success",
    )
    conn.commit()
    return summary


job_queue.register("exploratory.run", run_exploration)