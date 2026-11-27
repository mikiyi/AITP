"""评测集仓储。"""

from __future__ import annotations

from typing import Any
from .base import BaseRepository, loads


class EvalSuiteRepository(BaseRepository):
    table = "biz_eval_suite"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "visibility", "case_count", "created_at", "updated_at"}
    searchable = ["name", "description", "tags", "creator"]

    def get_detail(self, pk: int) -> dict[str, Any] | None:
        row = self.get(pk)
        if row is None:
            return None
        row["recentRuns"] = []
        return row

    def find_by_name(self, project_id: int, name: str) -> dict[str, Any] | None:
        return self.raw_one(
            "SELECT * FROM biz_eval_suite WHERE project_id = ? AND name = ? AND is_deleted = 0",
            (project_id, name),
        )


def suite_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "targetType": row.get("target_type"),
        "description": row.get("description"),
        "tags": row.get("tags"),
        "status": row.get("status"),
        "visibility": row.get("visibility"),
        "version": row.get("version"),
        "caseCount": row.get("case_count"),
        "creator": row.get("creator"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
