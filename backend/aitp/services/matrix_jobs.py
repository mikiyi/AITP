"""执行矩阵的队列处理器（GAP-43）。

矩阵一次调度只投递一个任务，任务内逐格真实执行并更新进度；
取消时把未跑格子标记跳过，矩阵状态收口为「已取消」。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from . import audit_service, execution_matrix_service, job_queue, task_control


def run_execution_matrix(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    matrix_id = int(payload.get("matrixId") or 0)
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
        summary = execution_matrix_service.execute_matrix(
            conn, matrix_id, db_path,
            on_progress=on_progress,
            cancel_check=lambda: task_control.is_cancelled(key),
        )
    except job_queue.TaskCancelled:
        execution_matrix_service.mark_cancelled(conn, matrix_id)
        raise
    except Exception:
        conn.rollback()
        row = execution_matrix_service.get_matrix(conn, matrix_id)
        if str(row.get("status") or "") == execution_matrix_service.MATRIX_RUNNING:
            from ..repositories.execution_matrix_repo import ExecutionMatrixRepository
            ExecutionMatrixRepository(conn).update(
                matrix_id, {"status": execution_matrix_service.MATRIX_FAILED}
            )
            conn.commit()
        raise
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        "execution_matrix",
        matrix_id,
        "执行矩阵调度",
        {
            "taskId": task_id,
            "cells": summary.get("cells"),
            "cellFailures": summary.get("cellFailures"),
            "stats": summary.get("stats"),
        },
        result="success" if not summary.get("cellFailures") else "failed",
    )
    conn.commit()
    return summary


job_queue.register("matrix.run", run_execution_matrix)
