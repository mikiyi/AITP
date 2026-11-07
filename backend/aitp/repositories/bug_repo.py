"""缺陷仓储 + 状态机。"""

from __future__ import annotations

from typing import Any

from ..common.enums import BUG_OPEN_STATUSES, BUG_TRANSITIONS
from ..common.pagination import RowDict, RowList
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from .base import BaseRepository, loads
from .numbering import next_no


class BugRepository(BaseRepository):
    table = "biz_bug"
    default_order = "created_at"
    order_whitelist = {
        "id", "bug_no", "title", "severity", "status", "priority",
        "bug_type", "assignee", "created_at", "updated_at",
    }
    searchable = ["bug_no", "title", "description", "module", "assignee"]

    def next_bug_no(self, project_id: int) -> str:
        return next_no(self.conn, self.table, "bug_no", "BUG", project_id)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["steps"] = loads(row.get("steps_json"), []) or []
        row.pop("steps_json", None)
        return row

    def transition(self, pk: int, to_status: str, operator: str = "", comment: str = "") -> dict[str, Any]:
        """按状态机流转，并写入流转日志。非法流转抛 140102 且不写日志。"""
        row = self.get(pk)
        if not row:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "缺陷不存在")

        from_status = row["status"]
        if to_status not in BUG_TRANSITIONS:
            raise AppError(ErrorCode.PARAM_ERROR, f"未知的缺陷状态：{to_status}")
        if to_status not in BUG_TRANSITIONS.get(from_status, ()):
            raise AppError(
                ErrorCode.ILLEGAL_TRANSITION,
                f"缺陷状态不能从 {from_status} 流转到 {to_status}",
            )

        self.update(pk, {"status": to_status})
        now = utcnow_iso()
        self.conn.execute(
            """
            INSERT INTO biz_bug_status_log
                (bug_id, from_status, to_status, operator, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (pk, from_status, to_status, operator, comment, now, now),
        )
        return {"id": pk, "fromStatus": from_status, "toStatus": to_status}

    def list_logs(self, bug_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_bug_status_log WHERE bug_id = ? ORDER BY id ASC", (bug_id,)
        ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "bugId": int(row["bug_id"]),
                "fromStatus": row["from_status"],
                "toStatus": row["to_status"],
                "operator": row["operator"],
                "comment": row["comment"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def link_cases(self, bug_id: int, case_ids: list[int], relation_type: str = "related") -> int:
        linked = 0
        for case_id in case_ids:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO biz_case_bug_rel
                    (case_id, bug_id, relation_type, created_at, updated_at)
                VALUES (?, ?, ?, datetime('now'), datetime('now'))
                """,
                (case_id, bug_id, relation_type),
            )
            linked += cursor.rowcount
        return linked

    def unlink_cases(self, bug_id: int, case_ids: list[int]) -> int:
        removed = 0
        for case_id in case_ids:
            cursor = self.conn.execute(
                "DELETE FROM biz_case_bug_rel WHERE bug_id = ? AND case_id = ?", (bug_id, case_id)
            )
            removed += cursor.rowcount
        return removed

    def linked_case_ids(self, bug_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT case_id FROM biz_case_bug_rel WHERE bug_id = ?", (bug_id,)
        ).fetchall()
        return [int(r["case_id"]) for r in rows]

    def status_counts(self, project_id: int | None = None, version_id: int | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS c FROM biz_bug WHERE is_deleted = 0"
        params: list[Any] = []
        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        if version_id is not None:
            sql += " AND version_id = ?"
            params.append(version_id)
        sql += " GROUP BY status"
        counts = {r["status"]: int(r["c"]) for r in self.conn.execute(sql, params).fetchall()}
        return counts

    def open_bug_count(self, project_id: int | None = None, version_id: int | None = None) -> int:
        placeholders = ", ".join("?" for _ in BUG_OPEN_STATUSES)
        sql = (
            "SELECT COUNT(*) FROM biz_bug WHERE is_deleted = 0"
            f" AND status IN ({placeholders})"
        )
        params: list[Any] = list(BUG_OPEN_STATUSES)
        if project_id is not None:
            sql += " AND project_id = ?"
            params.append(project_id)
        if version_id is not None:
            sql += " AND version_id = ?"
            params.append(version_id)
        return int(self.conn.execute(sql, params).fetchone()[0])
