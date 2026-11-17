"""P2 定时调度仓储。"""

from __future__ import annotations

from .base import BaseRepository


class TestScheduleRepository(BaseRepository):
    table = "biz_test_schedule"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "next_run_at", "created_at"}
    searchable = ["name"]