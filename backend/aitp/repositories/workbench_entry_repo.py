"""工作台编排入口仓储。

一个「入口」描述一条可复用的工作流：入口资产（触发词、必填信息、知识引用）+
按顺序执行的能力步骤。入口可发布版本，发布时版本号 +1，运行时记录用的是哪个版本。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import Page, RowDict, RowList
from .base import BaseRepository, loads

#: 入口状态
ENTRY_DRAFT = "draft"
ENTRY_PUBLISHED = "published"
ENTRY_ARCHIVED = "archived"

ENTRY_STATUSES = (ENTRY_DRAFT, ENTRY_PUBLISHED, ENTRY_ARCHIVED)


class WorkbenchEntryRepository(BaseRepository):
    table = "sys_workbench_entry"
    soft_delete_enabled = False
    default_order = "updated_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "code", "name", "capability", "version", "status", "updated_at"}
    searchable = ["code", "name", "description"]

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

    def get_by_code(self, code: str) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM sys_workbench_entry WHERE code = ?", (code,)
        ).fetchone()
        return _hydrate(dict(row)) if row else None

    def publish(self, pk: int) -> bool:
        """发布入口：版本号 +1，状态切到已发布。"""
        row = self.get(pk)
        if row is None:
            return False
        return self.update(pk, {
            "status": ENTRY_PUBLISHED,
            "version": int(row.get("version") or 1) + 1,
        })

    def versions_of(self, pk: int) -> RowList:
        current = self.get(pk)
        if current is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM sys_workbench_entry WHERE code = ? ORDER BY version DESC, id DESC",
            (str(current.get("code") or ""),),
        ).fetchall()
        return [dict(row) for row in rows]


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    """把 JSON 列还原成结构。"""
    row["triggers"] = loads(row.pop("triggers_json", None), []) or []
    row["required"] = loads(row.pop("required_json", None), []) or []
    row["knowledge"] = loads(row.pop("knowledge_json", None), []) or []
    row["workflow"] = loads(row.pop("workflow_json", None), []) or []
    return row


def entry_summary(row: dict[str, Any]) -> dict[str, Any]:
    """把一行入口转成前端用的结构。"""
    return {
        "id": row.get("id"),
        "code": row.get("code"),
        "name": row.get("name"),
        "capability": row.get("capability"),
        "description": row.get("description"),
        "triggers": row.get("triggers") or [],
        "required": row.get("required") or [],
        "knowledge": row.get("knowledge") or [],
        "workflow": row.get("workflow") or [],
        "version": row.get("version"),
        "status": row.get("status"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
