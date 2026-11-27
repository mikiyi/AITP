"""评测用例仓储。"""

from __future__ import annotations

from typing import Any
from .base import BaseRepository, loads


class EvalCaseRepository(BaseRepository):
    table = "biz_eval_case"
    default_order = "case_no"
    default_order_dir = "asc"
    order_whitelist = {"id", "case_no", "category", "severity", "difficulty", "created_at", "updated_at"}
    searchable = ["case_no", "title", "tags", "source_ref"]

    def next_case_no(self, suite_id: int) -> str:
        row = self.raw_one(
            "SELECT MAX(id) AS max_id, COUNT(*) AS total FROM biz_eval_case "
            "WHERE suite_id = ? AND is_deleted = 0",
            (suite_id,),
        )
        existing = int(row["total"] or 0) if row else 0
        return f"EC-{existing + 1:03d}"

    def count_by_suite(self, suite_id: int) -> int:
        return self.count({"suite_id": suite_id, "is_deleted": 0})

    def list_active(self, suite_id: int) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM biz_eval_case WHERE suite_id = ? AND status = 'active' AND is_deleted = 0 "
            "ORDER BY case_no",
            (suite_id,),
        )
        return [dict(row) for row in rows]

    def get_case(self, pk: int) -> dict[str, Any] | None:
        row = self.get(pk)
        if row is None:
            return None
        row["assertions"] = loads(row.pop("assertions_json", None), {}) or {}
        row["rubric"] = loads(row.pop("rubric_json", None), []) or []
        row["input"] = loads(row.pop("input_json", None), {}) or {}
        return row


def case_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "suiteId": row.get("suite_id"),
        "caseNo": row.get("case_no"),
        "category": row.get("category"),
        "title": row.get("title"),
        "severity": row.get("severity"),
        "expectedType": row.get("expected_type"),
        "tags": row.get("tags"),
        "source": row.get("source"),
        "effectiveDate": row.get("effective_date"),
        "status": row.get("status"),
        "createdAt": row.get("created_at"),
    }
