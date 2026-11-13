"""数据留存清理的队列处理器（GAP-44）。

到期数据按各类型配置的动作处理（归档/删除/仅留摘要），
被引用数据（缺陷引用的证据、矩阵引用的执行记录/轮次）到期不清并在结果里提示。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from . import audit_service, job_queue, retention_service


def run_retention_cleanup(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    data_types = payload.get("dataTypes")
    summary = retention_service.run_cleanup(
        conn, db_path,
        data_types=list(data_types) if isinstance(data_types, list) else None,
        created_by=str(payload.get("createdBy") or "任务队列"),
    )
    cleaned = sum(int(item.get("cleaned") or 0) for item in summary["types"])
    protected = len(summary.get("protected") or [])
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        "retention_cleanup",
        0,
        "数据留存到期清理",
        {
            "dryRun": bool(summary.get("dryRun")),
            "types": [
                {"dataType": item["dataType"], "due": item["due"],
                 "cleaned": item["cleaned"], "skipped": item["skipped"]}
                for item in summary["types"]
            ],
            "protected": summary.get("protected"),
        },
        result="success",
    )
    conn.commit()
    summary["totalCleaned"] = cleaned
    summary["totalProtected"] = protected
    return summary


job_queue.register("retention.cleanup", run_retention_cleanup)
