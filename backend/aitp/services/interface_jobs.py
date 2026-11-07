"""接口批量执行的队列处理器。

单个轮次里可能有多条接口用例，逐条发 HTTP 请求会占用请求线程；
交给任务队列后前端可以立即返回，任务中心能看到进度并可取消。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from . import audit_service, interface_runner, job_queue, task_control


def run_interface_batch(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    run_id = int(payload.get("runId") or 0)
    raw_env_id = payload.get("envId")
    raw_timeout = payload.get("timeoutSeconds")
    env_id = int(raw_env_id) if raw_env_id not in (None, "") else None
    timeout = int(raw_timeout) if raw_timeout not in (None, "") else interface_runner.DEFAULT_TIMEOUT
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

    summary = interface_runner.run_batch(
        conn,
        run_id,
        env_id=env_id,
        timeout=timeout,
        executed_by="任务队列",
        on_progress=on_progress,
    )
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        audit_service.RESOURCE_TEST_RUN,
        run_id,
        "接口用例批量执行",
        {
            "taskId": task_id,
            "total": summary["total"],
            "passed": summary["passed"],
            "failed": summary["failed"],
            "envId": summary.get("envId"),
        },
        result="success" if summary["failed"] == 0 else "failed",
    )
    conn.commit()
    return summary


job_queue.register("interface.batch_run", run_interface_batch)
