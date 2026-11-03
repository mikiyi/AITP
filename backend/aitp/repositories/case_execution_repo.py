"""用例执行记录仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from ..common.time_utils import utcnow_iso
from .base import BaseRepository


class CaseExecutionRepository(BaseRepository):
    table = "biz_case_execution"
    default_order = "executed_at"
    order_whitelist = {"id", "case_id", "result", "executed_at"}
    searchable = ["remark", "environment", "version"]

    def record(self, case_id: int, result: str, **fields: Any) -> int:
        payload = {
            "case_id": case_id,
            "result": result,
            "executed_at": fields.pop("executed_at", None) or utcnow_iso(),
            **fields,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.insert(payload)

    def latest_by_case(self, case_id: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_case_execution WHERE case_id = ? AND is_deleted = 0"
            " ORDER BY executed_at DESC, id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        return dict(row) if row else None

    def result_counts(self, project_id: int | None = None) -> dict[str, int]:
        sql = (
            "SELECT e.result, COUNT(*) AS c FROM biz_case_execution e"
            " JOIN biz_test_case c ON c.id = e.case_id"
            " WHERE e.is_deleted = 0 AND c.is_deleted = 0"
        )
        params: list[Any] = []
        if project_id is not None:
            sql += " AND c.project_id = ?"
            params.append(project_id)
        sql += " GROUP BY e.result"
        return {r["result"]: int(r["c"]) for r in self.conn.execute(sql, params).fetchall()}
