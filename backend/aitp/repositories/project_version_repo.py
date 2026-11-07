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

    #: 所有带 version_id 的业务表；删版本前必须一并检查，否则会留下悬空引用
    VERSION_TABLES = (
        "biz_requirement",
        "biz_test_case",
        "biz_bug",
        "biz_report",
        "biz_automation_script",
        "biz_test_data_batch",
        "biz_sql_analysis",
        "biz_test_plan",
        "biz_test_run",
        "biz_release_gate",
    )

    def count_assets(
        self, version_id: int, project_id: int | None = None
    ) -> int:
        """统计关联到该版本的资产/执行记录总数。

        给了 project_id 就只数本项目：版本号在跨项目引用校验尚未完全收口前可能
        被别的项目误挂，不限项目会把 A 的资产算进 B 版本的 assetCount。
        """
        scoped = project_id is not None and int(project_id) > 0
        clauses = [
            f"(SELECT COUNT(*) FROM {table} WHERE version_id = ? AND is_deleted = 0"
            + (" AND project_id = ?" if scoped else "")
            + ")"
            for table in self.VERSION_TABLES
        ]
        params: list[Any] = []
        for _ in self.VERSION_TABLES:
            params.append(version_id)
            if project_id is not None and int(project_id) > 0:
                params.append(int(project_id))
        row = self.conn.execute("SELECT " + " + ".join(clauses), params).fetchone()
        return int(row[0]) if row else 0
