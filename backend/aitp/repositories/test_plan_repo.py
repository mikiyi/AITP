"""测试计划仓储。

测试计划是「本次要测什么」的长期定义：项目、版本、默认环境、默认筛选范围。
真正执行时由计划生成测试轮次，计划本身不记录结果。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository

PLAN_ACTIVE = "active"
PLAN_ARCHIVED = "archived"
PLAN_STATUSES = (PLAN_ACTIVE, PLAN_ARCHIVED)


class TestPlanRepository(BaseRepository):
    table = "biz_test_plan"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "created_at", "updated_at"}
    searchable = ["name", "description", "owner"]

    def get_detail(self, pk: int) -> RowDict | None:
        return self.get(pk)

    def run_count(self, plan_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM biz_test_run WHERE plan_id = ? AND is_deleted = 0",
            (plan_id,),
        ).fetchone()
        return int(row[0]) if row else 0


def plan_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "versionId": row.get("version_id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "owner": row.get("owner"),
        "defaultEnvId": row.get("default_env_id"),
        "scope": row.get("scope"),
        "status": row.get("status"),
        "runCount": row.get("run_count"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
