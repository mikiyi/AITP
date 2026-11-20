"""P2 定时调度仓储。"""

from __future__ import annotations

from .base import BaseRepository
from .numbering import next_no


class TestScheduleRepository(BaseRepository):
    table = "biz_test_schedule"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "next_run_at", "created_at"}
    searchable = ["name", "schedule_no"]

    def next_schedule_no(self) -> str:
        return next_no(self.conn, self.table, "schedule_no", "SCH")