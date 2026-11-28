"""评测门禁仓储：阈值 JSON、基线与最近求值结论。"""

from __future__ import annotations

from typing import Any

from .base import BaseRepository, loads


class EvalGateRepository(BaseRepository):
    table = "biz_eval_gate"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "enabled", "last_evaluated_at", "created_at"}
    searchable = ["name"]

    def list_for_suite(self, suite_id: int) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM biz_eval_gate WHERE suite_id = ? AND is_deleted = 0 ORDER BY id",
            (suite_id,),
        )
        return [dict(row) for row in rows]

    def list_results(self, gate_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM biz_eval_gate_result WHERE gate_id = ? ORDER BY id DESC LIMIT ?",
            (gate_id, int(limit)),
        )
        return [dict(row) for row in rows]


def gate_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "suiteId": row.get("suite_id"),
        "name": row.get("name"),
        "thresholds": loads(row.get("thresholds_json"), {}) or {},
        "isHard": bool(row.get("is_hard", 1)),
        "baselineRunId": row.get("baseline_run_id"),
        "enabled": bool(row.get("enabled", 1)),
        "lastRunId": row.get("last_run_id"),
        "lastConclusion": row.get("last_conclusion"),
        "lastEvaluatedAt": row.get("last_evaluated_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
