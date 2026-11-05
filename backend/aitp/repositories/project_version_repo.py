"""项目版本仓储。

一个项目下可建多套版本（如 v1.0 / v2.0），各测试资产通过 version_id
关联到具体版本。version_id 可空表示「未分配版本」。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict, RowList
from .base import BaseRepository


class ProjectVersionRepository(BaseRepository):
    table = "biz_project_version"
    soft_delete_enabled = True
    default_order = "sort_order, id"
    default_order_dir = "asc"
    order_whitelist = {
        "id", "version_code", "name", "status", "sort_order",
        "created_at", "updated_at",
    }
    searchable = ["version_code", "name", "description"]

    def list_by_project(self, project_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_project_version"
            " WHERE project_id = ? AND is_deleted = 0"
            " ORDER BY sort_order ASC, id ASC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_project_version WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def code_exists(self, project_id: int, version_code: str, exclude_id: int | None = None) -> bool:
        sql = (
            "SELECT 1 FROM biz_project_version"
            " WHERE project_id = ? AND version_code = ? AND is_deleted = 0"
        )
        params: list[Any] = [project_id, version_code]
        if exclude_id is not None:
            sql += " AND id <> ?"
            params.append(exclude_id)
        return self.conn.execute(sql, params).fetchone() is not None

    def count_assets(self, version_id: int) -> int:
        """统计关联到该版本的资产总数（需求+用例+缺陷+报告）。"""
        row = self.conn.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM biz_requirement WHERE version_id = ? AND is_deleted = 0) +
              (SELECT COUNT(*) FROM biz_test_case WHERE version_id = ? AND is_deleted = 0) +
              (SELECT COUNT(*) FROM biz_bug WHERE version_id = ? AND is_deleted = 0) +
              (SELECT COUNT(*) FROM biz_report WHERE version_id = ? AND is_deleted = 0)
            """,
            (version_id, version_id, version_id, version_id),
        ).fetchone()
        return int(row[0]) if row else 0
