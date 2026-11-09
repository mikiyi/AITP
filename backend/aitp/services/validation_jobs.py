"""案例验证全量跑批的队列处理器。

`run-all` 会逐条跑启用的验证案例，而案例一旦带能力期望就会真的调模型（一条几十秒、
一批就是十几分钟）。留在请求线程里有两重代价：占住 waitress 的工作线程，以及整批
只在最后 commit 一次、把 SQLite 写锁按住全程。改成队列任务后接口立刻返回 taskId，
每条案例跑完单独提交（写锁只在两次之间短暂持有），任务中心能看到进度也可以取消。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from ..repositories.validation_case_repo import ValidationCaseRepository
from . import audit_service, job_queue, task_control, validation_service

#: 队列任务类型；中文标签登记在 job_queue.KIND_LABELS
RUN_ALL_KIND = "validation.run_all"
RUN_ALL_TITLE = "案例验证全量跑批"


def submit_run_all(
    conn: sqlite3.Connection,
    *,
    project_id: int | None = None,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """入队一次全量验证并返回任务快照，不等案例跑完。

    没有启用的案例时照样入队：任务会立即以 total=0 收尾，与改造前的同步口径一致，
    省掉「点了按钮报参数错误」这种平白多出来的失败面。
    """
    total = len(ValidationCaseRepository(conn).enabled_cases())
    task = job_queue.submit(
        conn,
        kind=RUN_ALL_KIND,
        title=RUN_ALL_TITLE,
        payload={"projectId": project_id, "profileId": profile_id},
        priority=200,
    )
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_GENERATE,
        audit_service.RESOURCE_VALIDATION_CASE,
        None,
        "全量验证",
        {"mode": "async", "taskId": task.get("id"), "total": total},
    )
    conn.commit()
    return task


def run_validation_all(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    """逐条跑启用的案例，每条跑完立即提交并回写进度。"""
    task_id = int(task["id"])
    key = f"task:{task_id}"
    project_id = _int_or_none(payload.get("projectId"))
    profile_id = _int_or_none(payload.get("profileId"))
    rows = ValidationCaseRepository(conn).enabled_cases()
    total = len(rows)
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if task_control.is_cancelled(key):
            raise job_queue.TaskCancelled()
        case_id = int(row["id"])
        outcome = validation_service.run_case(
            conn, case_id, project_id=project_id, profile_id=profile_id,
        )
        results.append({
            "id": case_id,
            "name": row.get("name"),
            "passed": outcome["passed"],
            "errors": outcome["errors"],
        })
        conn.commit()
        _progress(conn, task_id, int(index * 100 / total))
    return validation_service.summarize(results)


def _progress(conn: sqlite3.Connection, task_id: int, value: int) -> None:
    conn.execute(
        "UPDATE sys_task SET progress = ?, updated_at = ? WHERE id = ?",
        (min(99, int(value)), utcnow_iso(), task_id),
    )
    conn.commit()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


job_queue.register(RUN_ALL_KIND, run_validation_all)
