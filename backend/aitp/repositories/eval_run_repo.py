"""评测跑批仓储。"""

from __future__ import annotations

from typing import Any
from .base import BaseRepository


class EvalRunRepository(BaseRepository):
    soft_delete_enabled = False  # 结果类表无软删列
    table = "biz_eval_run"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {
        "id",
        "status",
        "created_at",
        "finished_at",
        "success_rate",
        "total",
        "gate_conclusion",
    }
    searchable = ["status", "gate_conclusion", "creator", "ablation_group", "run_purpose"]

    def find_by_run_key(self, run_key: str) -> dict[str, Any] | None:
        return self.raw_one("SELECT * FROM biz_eval_run WHERE run_key = ?", (run_key,))

    def summary(self, pk: int) -> dict[str, Any] | None:
        row = self.get(pk)
        if row is None:
            return None
        return {
            "id": row.get("id"),
            "projectId": row.get("project_id"),
            "suiteId": row.get("suite_id"),
            "suiteVersion": row.get("suite_version"),
            "judgeMode": row.get("judge_mode"),
            "status": row.get("status"),
            "total": row.get("total"),
            "passed": row.get("passed"),
            "failed": row.get("failed"),
            "skipped": row.get("skipped"),
            "errors": row.get("errors"),
            "judgeInvalid": row.get("judge_invalid"),
            "successRate": row.get("success_rate"),
            "gateConclusion": row.get("gate_conclusion"),
            "caseSetHash": row.get("case_set_hash"),
            "runKey": row.get("run_key"),
            "costMicros": row.get("cost_micros"),
            "costSource": row.get("cost_source"),
            "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"),
            "createdAt": row.get("created_at"),
        }

    def latest_by_suites(self, suite_ids: list[int]) -> dict[int, dict[str, Any]]:
        """批量取每个评测集最近一条跑批（含未结束的），供列表页展示健康度。"""

        if not suite_ids:
            return {}
        placeholders = ",".join("?" for _ in suite_ids)
        rows = self.raw(
            "SELECT * FROM biz_eval_run WHERE id IN ("
            f"SELECT MAX(id) FROM biz_eval_run WHERE suite_id IN ({placeholders}) GROUP BY suite_id"
            ")",
            tuple(suite_ids),
        )
        return {int(row["suite_id"]): dict(row) for row in rows}


def run_brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """列表页用的精简跑批摘要；完整字段走 run 详情的 summary()。"""

    if row is None:
        return None
    return {
        "id": row.get("id"),
        "status": row.get("status"),
        "total": row.get("total"),
        "passed": row.get("passed"),
        "failed": row.get("failed"),
        "successRate": row.get("success_rate"),
        "gateConclusion": row.get("gate_conclusion"),
        "finishedAt": row.get("finished_at"),
        "createdAt": row.get("created_at"),
    }
