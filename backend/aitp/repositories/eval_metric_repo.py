"""评测指标仓储。"""

from __future__ import annotations

from typing import Any

from .base import BaseRepository


class EvalMetricRepository(BaseRepository):
    soft_delete_enabled = False  # 结果类表无软删列
    table = "biz_eval_metric"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "metric_key", "value", "sample_size", "created_at"}
    searchable = ["metric_key"]

    def map_by_run(self, run_id: int) -> dict[str, dict[str, Any]]:
        rows = self.raw("SELECT * FROM biz_eval_metric WHERE run_id = ?", (run_id,))
        return {str(row["metric_key"]): dict(row) for row in rows}

    def replace_all(self, run_id: int, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM biz_eval_metric WHERE run_id = ?", (run_id,))
        for row in rows:
            self.insert({**row, "run_id": run_id})
