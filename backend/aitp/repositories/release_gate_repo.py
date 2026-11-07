"""发布准入仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository, loads


class ReleaseGateRepository(BaseRepository):
    table = "biz_release_gate"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "created_at", "updated_at"}
    searchable = ["name", "conclusion", "created_by"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["thresholds"] = loads(row.pop("thresholds_json", None), {}) or {}
        row["result"] = loads(row.pop("result_json", None), {}) or {}
        return row


def gate_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "versionId": row.get("version_id"),
        "runId": row.get("run_id"),
        "name": row.get("name"),
        "status": row.get("status"),
        "thresholds": row.get("thresholds") or {},
        "result": row.get("result") or {},
        "conclusion": row.get("conclusion"),
        "createdBy": row.get("created_by"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
