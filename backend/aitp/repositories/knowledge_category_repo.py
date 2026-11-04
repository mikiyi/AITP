"""知识库分类仓储。

分类值直接就是中文名（文章表 category 存的就是它），这张表负责
「有哪些可选分类」与排序；自定义分类只是多一行，删除只做软删，
已归类的文章不受影响。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository

#: 默认分类：找不到或留空时落到这里
DEFAULT_CATEGORY = "通用"

#: 内置分类，不允许删除
SYSTEM_CATEGORIES = ("通用", "测试方法", "经验总结", "踩坑记录", "会议纪要", "安全测试")


def decorate(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "sortOrder": int(row.get("sort_order") or 0),
        "isSystem": bool(row.get("is_system")),
        "createdAt": row.get("created_at"),
    }


class KnowledgeCategoryRepository(BaseRepository):
    table = "sys_knowledge_category"
    default_order = "sort_order"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "sort_order", "created_at"}
    searchable = ["name"]

    def list_categories(self) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM sys_knowledge_category WHERE is_deleted = 0 "
            "ORDER BY sort_order ASC, id ASC"
        )
        return [decorate(dict(row)) for row in rows]

    def find_by_name(self, name: str) -> RowDict | None:
        return self.raw_one(
            "SELECT * FROM sys_knowledge_category WHERE name = ? AND is_deleted = 0",
            (name,),
        )

    def next_sort_order(self) -> int:
        row = self.raw_one(
            "SELECT MAX(sort_order) AS max_order FROM sys_knowledge_category WHERE is_deleted = 0"
        )
        return int((row or {}).get("max_order") or 0) + 10
