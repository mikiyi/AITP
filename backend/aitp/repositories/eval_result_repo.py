"""评测逐条结果仓储。"""

from __future__ import annotations

from typing import Any
from .base import BaseRepository


class EvalResultRepository(BaseRepository):
    soft_delete_enabled = False  # 结果类表无软删列
    table = "biz_eval_result"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "verdict", "latency_ms", "created_at"}
    searchable = ["case_no", "verdict", "failure_reason"]

    def list_by_run(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM biz_eval_result WHERE run_id = ? ORDER BY id",
            (run_id,),
        )
        return [dict(row) for row in rows]

    def verdict_counts(self, run_id: int) -> dict[str, int]:
        rows = self.raw(
            "SELECT verdict, COUNT(*) AS total FROM biz_eval_result WHERE run_id = ? GROUP BY verdict",
            (run_id,),
        )
        return {str(row["verdict"]): int(row["total"]) for row in rows}
