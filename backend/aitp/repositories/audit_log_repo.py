"""审计日志仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowList
from .base import BaseRepository, loads


class AuditLogRepository(BaseRepository):
    table = "sys_audit_log"
    soft_delete_enabled = False
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "action", "resource_type", "result", "created_at"}

    def list_with_detail(self, page_num: int = 1, page_size: int = 20) -> dict[str, Any]:
        page = self.list(None, None, page_num=page_num, page_size=page_size)
        for row in page.list:
            row["detail"] = loads(row.pop("detail_json", None), {}) or {}
        return page.as_dict()

    def list_by_resource(self, resource_type: str, resource_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM sys_audit_log WHERE resource_type = ? AND resource_id = ?"
            " ORDER BY id DESC",
            (resource_type, resource_id),
        ).fetchall()
        return [dict(r) for r in rows]
