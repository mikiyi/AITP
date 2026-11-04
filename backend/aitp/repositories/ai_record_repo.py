"""AI 执行记录仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository, loads


class AIRecordRepository(BaseRepository):
    table = "biz_ai_record"
    default_order = "created_at"
    order_whitelist = {
        "id", "capability", "risk_level", "review_status", "created_at", "updated_at",
    }
    searchable = ["summary", "capability"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["evidence"] = loads(row.pop("evidence_json", None), []) or []
        row["missing"] = loads(row.pop("missing_json", None), []) or []
        row["risks"] = loads(row.pop("risk_json", None), []) or []
        return row

    def decorate(self, row: RowDict) -> dict[str, Any]:
        """把一行记录转成前端用的结构。"""
        return {
            "id": row.get("id"),
            "taskId": row.get("task_id"),
            "sessionId": row.get("session_id"),
            "capability": row.get("capability"),
            "capabilityLabel": row.get("capability_label"),
            "model": row.get("model"),
            "summary": row.get("summary"),
            "evidence": row.get("evidence") or loads(row.get("evidence_json"), []) or [],
            "missing": row.get("missing") or loads(row.get("missing_json"), []) or [],
            "risks": row.get("risks") or loads(row.get("risk_json"), []) or [],
            "riskLevel": row.get("risk_level"),
            "reviewRequired": bool(row.get("review_required")),
            "reviewStatus": row.get("review_status"),
            "reviewNote": row.get("review_note"),
            "reviewedAt": row.get("reviewed_at"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def count_by_review(self, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM biz_ai_record WHERE review_status = ? AND is_deleted = 0",
            (status,),
        ).fetchone()
        return int(row[0])
