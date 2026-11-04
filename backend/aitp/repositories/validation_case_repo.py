"""验证案例仓储。

验证案例是「路由与安全规则」的回归夹具：给一段输入，
期望命中哪些能力、期望出现哪些缺项、期望触发哪些行为安全规则。
跑一次沙箱就能知道行为有没有漂移。
"""

from __future__ import annotations

import json
from typing import Any

from ..common.pagination import Page, RowDict, RowList
from ..common.time_utils import utcnow_iso
from .base import BaseRepository, loads

#: 案例状态
CASE_PUBLISHED = "published"
CASE_DISABLED = "disabled"

CASE_STATUSES = (CASE_PUBLISHED, CASE_DISABLED)


class ValidationCaseRepository(BaseRepository):
    table = "sys_validation_case"
    soft_delete_enabled = False
    default_order = "updated_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "version", "updated_at"}
    searchable = ["name", "description", "input_text"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        return _hydrate(row)

    def list_detail(
        self,
        filters: dict[str, Any] | None = None,
        keyword: str | None = None,
        page_num: int = 1,
        page_size: int = 20,
    ) -> Page:
        page = self.list(filters, keyword, page_num=page_num, page_size=page_size)
        for row in page.list:
            _hydrate(row)
        return page

    def save_result(self, pk: int, result: dict[str, Any]) -> bool:
        row = self.get(pk)
        payload: dict[str, Any] = {
            "last_result_json": json.dumps(result, ensure_ascii=False),
            "updated_at": utcnow_iso(),
        }
        if row is not None:
            payload["version"] = int(row.get("version") or 1)
        return self.update(pk, payload)

    def enabled_cases(self) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM sys_validation_case WHERE status = ? ORDER BY id ASC",
            (CASE_PUBLISHED,),
        ).fetchall()
        return [_hydrate(dict(row)) for row in rows]


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    row["variables"] = loads(row.pop("variables_json", None), {}) or {}
    row["expectedRoutes"] = loads(row.pop("expected_routes_json", None), []) or []
    row["expectedMissing"] = loads(row.pop("expected_missing_json", None), []) or []
    row["expectedRules"] = loads(row.pop("expected_rules_json", None), []) or []
    row["lastResult"] = loads(row.pop("last_result_json", None), {}) or {}
    return row


def case_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "inputText": row.get("input_text"),
        "capability": row.get("capability"),
        "variables": row.get("variables") or {},
        "expectedRoutes": row.get("expectedRoutes") or [],
        "expectedMissing": row.get("expectedMissing") or [],
        "expectedRules": row.get("expectedRules") or [],
        "status": row.get("status"),
        "lastResult": row.get("lastResult") or {},
        "version": row.get("version"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
