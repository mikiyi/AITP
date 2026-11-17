"""残留子项：独立数据池仓储。"""

from __future__ import annotations

from .base import BaseRepository


class DataPoolRepository(BaseRepository):
    table = "biz_data_pool"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "created_at"}
    searchable = ["name", "description"]