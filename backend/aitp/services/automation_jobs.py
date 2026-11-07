"""任务队列里的作业处理器。

每个处理器都在 worker 线程里执行：拿到独立连接、任务行、payload 与库路径，
返回可序列化的结果字典；抛异常由队列统一记为失败/重试。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import (
    SCRIPT_STATUS_DISABLED,
    AutomationScriptRepository,
)
from . import audit_service, automation_service, job_queue, notification_service, task_control


def run_automation_script(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    script_id = int(payload.get("scriptId") or 0)
    script = AutomationScriptRepository(conn).get(script_id)
    if script is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    if str(script.get("status") or "") == SCRIPT_STATUS_DISABLED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "脚本已停用，先启用再执行")

    category = str(script.get("category") or "web")
    automation_service.require_executable(conn, category, str(script.get("framework") or ""))
    timeout = payload.get("timeoutSeconds")
    test_run_id = int(payload["testRunId"]) if payload.get("testRunId") else None
    result = automation_service.execute_script(
        conn,
        script,
        db_path,
        int(timeout) if timeout else None,
        test_run_id,
        cancel_check=lambda: task_control.is_cancelled(f"task:{int(task['id'])}"),
    )
    run_id = int(result.get("id") or 0)
    conn.execute(
        "UPDATE biz_automation_run SET env_id = ?, task_id = ? WHERE id = ?",
        (script.get("env_id"), int(task["id"]), run_id),
    )
    conn.commit()
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        audit_service.RESOURCE_AUTOMATION_RUN,
        run_id,
        str(script.get("name") or ""),
        {
            "scriptNo": script.get("script_no"),
            "category": category,
            "status": result.get("status"),
            "taskId": int(task["id"]),
            "testRunId": test_run_id,
            "writebackStatus": result.get("writeback_status"),
            "async": True,
        },
        result="success" if result.get("status") == "success" else "failed",
    )
    notification_service.notify_automation_failed(conn, script, result)
    conn.commit()
    return {
        "runId": run_id,
        "runNo": result.get("run_no"),
        "status": result.get("status"),
        "durationMs": result.get("duration_ms"),
        "testRunId": test_run_id,
        "writebackStatus": result.get("writeback_status"),
        "writebackSummary": result.get("writeback_summary"),
    }


job_queue.register("automation.run", run_automation_script)
