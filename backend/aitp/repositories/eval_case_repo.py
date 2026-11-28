"""评测用例仓储。"""

from __future__ import annotations

from typing import Any
from .base import BaseRepository, loads
from .numbering import begin_claim


class EvalCaseRepository(BaseRepository):
    table = "biz_eval_case"
    default_order = "case_no"
    default_order_dir = "asc"
    order_whitelist = {"id", "case_no", "category", "severity", "difficulty", "created_at", "updated_at"}
    searchable = ["case_no", "title", "tags", "source_ref"]

    def next_case_no(self, suite_id: int) -> str:
        """EC 编号按集自增不复用：软删的行也参与取号（按最大编号 +1）。

        并发口径同 numbering.next_case_no：先 begin_claim 把读放进写事务，
        取号与紧随其后的插入共用同一次持锁，避免并发撞 uk_biz_eval_case_no。
        """

        begin_claim(self.conn)
        rows = self.raw("SELECT case_no FROM biz_eval_case WHERE suite_id = ?", (suite_id,))
        max_no = 0
        for row in rows:
            text = str(row["case_no"] or "")
            if not text.startswith("EC-"):
                continue
            try:
                max_no = max(max_no, int(text[3:]))
            except ValueError:
                continue
        return f"EC-{max_no + 1:03d}"

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


def case_detail(row: dict[str, Any]) -> dict[str, Any]:
    """编辑用明细：列表带 detail=1 时返回，避免为单条读取再开一条路由。"""

    return case_summary(row) | {
        "expectedText": row.get("expected_text") or "",
        "input": loads(row.get("input_json"), {}) or {},
        "assertions": loads(row.get("assertions_json"), {}) or {},
        "rubric": loads(row.get("rubric_json"), []) or [],
        "relevantDocs": loads(row.get("relevant_docs"), []) or [],
        "gradedRelevance": loads(row.get("graded_relevance"), {}) or {},
        "toolsExpected": loads(row.get("tools_expected"), {}) or {},
        "difficulty": row.get("difficulty"),
        "sourceRef": row.get("source_ref") or "",
        "creator": row.get("creator"),
        "updatedAt": row.get("updated_at"),
    }
