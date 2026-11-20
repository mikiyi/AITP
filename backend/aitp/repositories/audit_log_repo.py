"""审计日志仓储。"""

from __future__ import annotations

from ..common.pagination import RowList
from .base import BaseRepository


class AuditLogRepository(BaseRepository):
    table = "sys_audit_log"
    soft_delete_enabled = False
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "action", "resource_type", "result", "created_at"}

    def list_by_resource(self, resource_type: str, resource_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM sys_audit_log WHERE resource_type = ? AND resource_id = ?"
            " ORDER BY id DESC",
            (resource_type, resource_id),
        ).fetchall()
        return [dict(r) for r in rows]
