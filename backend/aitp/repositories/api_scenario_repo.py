"""多接口串联场景资产仓储。"""

from __future__ import annotations

from .base import BaseRepository
from .numbering import next_no


class ApiScenarioRepository(BaseRepository):
    table = "biz_api_scenario"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "scenario_no", "name", "created_at", "updated_at"}
    searchable = ["name", "scenario_no", "description"]

    def next_scenario_no(self) -> str:
        return next_no(self.conn, self.table, "scenario_no", "SCN")


class ApiScenarioRunRepository(BaseRepository):
    table = "biz_api_scenario_run"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "run_no", "created_at"}
    soft_delete_enabled = False

    def next_run_no(self) -> str:
        return next_no(self.conn, self.table, "run_no", "SCNR")