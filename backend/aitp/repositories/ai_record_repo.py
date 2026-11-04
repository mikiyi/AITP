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
        row["input"] = loads(row.pop("input_json", None), {}) or {}
        row["route"] = loads(row.pop("route_json", None), []) or []
        row["usage"] = loads(row.pop("usage_json", None), {}) or {}
        row["steps"] = loads(row.pop("steps_json", None), []) or []
        return row

    def decorate(self, row: RowDict, *, full: bool = False) -> dict[str, Any]:
        """把一行记录转成前端用的结构。

        列表只需要摘要，产出原文可能很长，默认不带；
        详情页和能力页的历史回看要走 ``full=True``。
        """
        return {
            "id": row.get("id"),
            "taskId": row.get("task_id"),
            "sessionId": row.get("session_id"),
            "capability": row.get("capability"),
            "capabilityLabel": row.get("capability_label"),
            "model": row.get("model"),
            "summary": row.get("summary"),
            "outputText": str(row.get("output_text") or "") if full else "",
            "evidence": row.get("evidence") or loads(row.get("evidence_json"), []) or [],
            "missing": row.get("missing") or loads(row.get("missing_json"), []) or [],
            "risks": row.get("risks") or loads(row.get("risk_json"), []) or [],
            "riskLevel": row.get("risk_level"),
            "reviewRequired": bool(row.get("review_required")),
            "reviewStatus": row.get("review_status"),
            "reviewNote": row.get("review_note"),
            "reviewedAt": row.get("reviewed_at"),
            "input": row.get("input") or loads(row.get("input_json"), {}) or {},
            "route": row.get("route") or loads(row.get("route_json"), []) or [],
            "steps": row.get("steps") or loads(row.get("steps_json"), []) or [],
            "usage": row.get("usage") or loads(row.get("usage_json"), {}) or {},
            "promptCode": row.get("prompt_code"),
            "latencyMs": row.get("latency_ms"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def count_by_review(self, status: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM biz_ai_record WHERE review_status = ? AND is_deleted = 0",
            (status,),
        ).fetchone()
        return int(row[0])
