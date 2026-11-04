"""AI 任务步骤轨迹仓储。

一次「多入口顺序执行」会派生多个子任务，每个子任务对应一条步骤轨迹：
步骤名、输入摘要、输出摘要、模型、耗时、状态、错误。
轨迹是真实执行的结果，不是执行前预填的静态列表。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict, RowList
from .base import BaseRepository

#: 步骤状态
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCESS = "success"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"

STEP_STATUSES = (STEP_PENDING, STEP_RUNNING, STEP_SUCCESS, STEP_FAILED, STEP_SKIPPED)


class AIStepRepository(BaseRepository):
    table = "biz_ai_step"
    default_order = "step_order"
    default_order_dir = "asc"
    order_whitelist = {"id", "task_id", "capability", "step_order", "status", "created_at"}
    searchable = ["step_name", "capability"]

    def list_by_task(self, task_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_ai_step WHERE task_id = ? AND is_deleted = 0"
            " ORDER BY step_order ASC, id ASC",
            (task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_by_parent(self, parent_task_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_ai_step WHERE parent_task_id = ? AND is_deleted = 0"
            " ORDER BY step_order ASC, id ASC",
            (parent_task_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_running(self, pk: int, input_summary: str = "") -> bool:
        return self.update(pk, {
            "status": STEP_RUNNING,
            "input_summary": input_summary,
            "started_at": _now(),
        })

    def mark_success(
        self,
        pk: int,
        *,
        output_summary: str,
        model: str,
        latency_ms: int,
    ) -> bool:
        return self.update(pk, {
            "status": STEP_SUCCESS,
            "output_summary": output_summary,
            "model": model,
            "latency_ms": latency_ms,
            "completed_at": _now(),
        })

    def mark_failed(self, pk: int, *, error_code: int | None, error_message: str) -> bool:
        payload: dict[str, Any] = {
            "status": STEP_FAILED,
            "error_message": error_message,
            "completed_at": _now(),
        }
        if error_code is not None:
            payload["error_code"] = error_code
        return self.update(pk, payload)

    def mark_skipped(self, pk: int, reason: str) -> bool:
        return self.update(pk, {
            "status": STEP_SKIPPED,
            "error_message": reason,
            "completed_at": _now(),
        })


def _now() -> str:
    from ..common.time_utils import utcnow_iso

    return utcnow_iso()


def step_summary(row: dict[str, Any]) -> dict[str, Any]:
    """把一行步骤转成前端用的结构。"""
    return {
        "id": row.get("id"),
        "taskId": row.get("task_id"),
        "parentTaskId": row.get("parent_task_id"),
        "capability": row.get("capability"),
        "stepOrder": row.get("step_order"),
        "stepName": row.get("step_name"),
        "status": row.get("status"),
        "inputSummary": row.get("input_summary"),
        "outputSummary": row.get("output_summary"),
        "model": row.get("model"),
        "latencyMs": row.get("latency_ms"),
        "errorCode": row.get("error_code"),
        "errorMessage": row.get("error_message"),
        "startedAt": row.get("started_at"),
        "completedAt": row.get("completed_at"),
    }
