"""需求仓储。"""

from __future__ import annotations

from ..common.pagination import RowList
from .base import BaseRepository
from .numbering import next_no


class RequirementRepository(BaseRepository):
    table = "biz_requirement"
    default_order = "created_at"
    order_whitelist = {"id", "req_no", "title", "status", "priority", "created_at", "updated_at"}
    searchable = ["req_no", "title", "content", "tags"]

    def next_req_no(self, project_id: int) -> str:
        return next_no(self.conn, self.table, "req_no", "REQ", project_id)

    def list_by_project(self, project_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_requirement WHERE project_id = ? AND is_deleted = 0"
            " ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
