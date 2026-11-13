"""报告片段与片段使用关系（三期 GAP-42）。

片段本体只在来源项目维护一份；目标报告只登记使用关系，
渲染/导出时实时读取片段正文，避免「插入时复制一份、之后各处漂移」。
"""

from __future__ import annotations

from ..common.pagination import RowDict
from ..errors import AppError, ErrorCode
from .base import BaseRepository


class ReportFragmentRepository(BaseRepository):
    table = "biz_report_fragment"
    default_order = "updated_at"
    order_whitelist = {"id", "title", "section", "is_global", "created_at", "updated_at"}
    searchable = ["title", "content", "source_note"]


class ReportFragmentUseRepository(BaseRepository):
    table = "biz_report_fragment_use"
    default_order = "created_at"
    order_whitelist = {"id", "fragment_id", "report_id", "created_at"}

    def add_use(self, fragment_id: int, report_id: int, inserted_by: str = "") -> int:
        row = self.conn.execute(
            "SELECT id, is_deleted FROM biz_report_fragment_use"
            " WHERE fragment_id = ? AND report_id = ?",
            (fragment_id, report_id),
        ).fetchone()
        if row:
            if int(row["is_deleted"] or 0):
                self.update(int(row["id"]), {"is_deleted": 0, "inserted_by": inserted_by})
            return int(row["id"])
        return self.insert({
            "fragment_id": fragment_id,
            "report_id": report_id,
            "inserted_by": inserted_by,
        })

    def remove_use(self, fragment_id: int, report_id: int) -> bool:
        cursor = self.conn.execute(
            "UPDATE biz_report_fragment_use SET is_deleted = 1"
            " WHERE fragment_id = ? AND report_id = ? AND is_deleted = 0",
            (fragment_id, report_id),
        )
        return cursor.rowcount > 0

    def remove_by_report(self, report_id: int) -> int:
        cursor = self.conn.execute(
            "UPDATE biz_report_fragment_use SET is_deleted = 1"
            " WHERE report_id = ? AND is_deleted = 0",
            (report_id,),
        )
        return cursor.rowcount

    def fragment_ids(self, report_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT fragment_id FROM biz_report_fragment_use"
            " WHERE report_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (report_id,),
        ).fetchall()
        return [int(row["fragment_id"]) for row in rows]

    def report_ids(self, fragment_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT report_id FROM biz_report_fragment_use"
            " WHERE fragment_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (fragment_id,),
        ).fetchall()
        return [int(row["report_id"]) for row in rows]

    def count_by_fragment(self, fragment_id: int) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM biz_report_fragment_use"
            " WHERE fragment_id = ? AND is_deleted = 0",
            (fragment_id,),
        ).fetchone()[0])

    def ensure_not_used(self, fragment_id: int, action: str = "删除") -> None:
        rows = self.conn.execute(
            "SELECT u.report_id, r.title FROM biz_report_fragment_use u"
            " LEFT JOIN biz_report r ON r.id = u.report_id"
            " WHERE u.fragment_id = ? AND u.is_deleted = 0 ORDER BY u.id ASC",
            (fragment_id,),
        ).fetchall()
        if not rows:
            return
        used_by = [
            {"reportId": int(row["report_id"]), "reportTitle": str(row["title"] or "")}
            for row in rows
        ]
        raise AppError(
            ErrorCode.RESOURCE_REFERENCED,
            f"该片段已被 {len(used_by)} 份报告使用，禁止{action}",
            {"reports": used_by},
        )


def fragment_public(row: RowDict, *, source_project_name: str = "",
                    source_report_title: str = "", use_count: int = 0) -> RowDict:
    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "sourceProjectName": source_project_name,
        "title": str(row["title"] or ""),
        "section": str(row["section"] or "overview"),
        "content": str(row["content"] or ""),
        "sourceReportId": row.get("source_report_id"),
        "sourceReportTitle": source_report_title,
        "sourceNote": str(row.get("source_note") or ""),
        "isGlobal": bool(row.get("is_global")),
        "createdBy": str(row.get("created_by") or ""),
        "useCount": int(use_count),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
