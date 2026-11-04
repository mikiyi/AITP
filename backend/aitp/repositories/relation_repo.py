"""通用实体关联仓储。

需求、用例、缺陷、模块、知识、报告之间本来各有一套关联方式，
这里提供一张通用的关联表，任何两种实体都能挂上关系，
前端「关联页」按来源实体拉出一张关系网。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import Page, RowDict, RowList
from .base import BaseRepository

#: 可关联的实体类型
ENTITY_TYPES = (
    "requirement",
    "module",
    "case",
    "bug",
    "knowledge",
    "report",
    "regression",
    "ai_record",
)

#: 关系类型
RELATION_RELATES = "relates"
RELATION_VERIFIES = "verifies"
RELATION_BLOCKS = "blocks"
RELATION_DERIVED_FROM = "derived_from"
RELATION_COVERS = "covers"
RELATION_DUPLICATES = "duplicates"

RELATION_TYPES = (
    RELATION_RELATES,
    RELATION_VERIFIES,
    RELATION_BLOCKS,
    RELATION_DERIVED_FROM,
    RELATION_COVERS,
    RELATION_DUPLICATES,
)


class EntityRelationRepository(BaseRepository):
    table = "biz_entity_relation"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "source_type", "relation_type", "target_type", "created_at"}
    searchable = ["remark"]

    def list_related(self, entity_type: str, entity_id: int) -> RowList:
        """列出与某实体有关的所有关联（双向）。"""
        rows = self.conn.execute(
            "SELECT * FROM biz_entity_relation WHERE is_deleted = 0"
            " AND ((source_type = ? AND source_id = ?) OR (target_type = ? AND target_id = ?))"
            " ORDER BY id DESC",
            (entity_type, entity_id, entity_type, entity_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def exists(self, source_type: str, source_id: int,
               relation_type: str, target_type: str, target_id: int) -> bool:
        row = self.conn.execute(
            "SELECT id FROM biz_entity_relation WHERE is_deleted = 0"
            " AND source_type = ? AND source_id = ? AND relation_type = ?"
            " AND target_type = ? AND target_id = ?",
            (source_type, source_id, relation_type, target_type, target_id),
        ).fetchone()
        return row is not None

    def list_detail(self, filters: dict[str, Any] | None = None,
                    keyword: str | None = None,
                    page_num: int = 1, page_size: int = 20) -> Page:
        return self.list(filters, keyword, page_num=page_num, page_size=page_size)

    def count_by_type(self, entity_type: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM biz_entity_relation WHERE is_deleted = 0"
            " AND (source_type = ? OR target_type = ?)",
            (entity_type, entity_type),
        ).fetchone()
        return int(row[0])


def relation_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "sourceType": row.get("source_type"),
        "sourceId": row.get("source_id"),
        "relationType": row.get("relation_type"),
        "targetType": row.get("target_type"),
        "targetId": row.get("target_id"),
        "remark": row.get("remark"),
        "createdAt": row.get("created_at"),
    }
