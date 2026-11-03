"""项目仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository


class ProjectRepository(BaseRepository):
    table = "biz_project"
    default_order = "created_at"
    order_whitelist = {"id", "code", "name", "status", "created_at", "updated_at"}
    searchable = ["code", "name", "description"]

    def get_by_code(self, code: str) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_project WHERE code = ? AND is_deleted = 0", (code,)
        ).fetchone()
        return dict(row) if row else None

    def code_exists(self, code: str, exclude_id: int | None = None) -> bool:
        sql = "SELECT 1 FROM biz_project WHERE code = ? AND is_deleted = 0"
        params: list[Any] = [code]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        return self.conn.execute(sql, params).fetchone() is not None
