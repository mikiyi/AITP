"""知识文章仓储，含 FTS5 混合检索与撤销/删除的引用保护。"""

from __future__ import annotations

from typing import Any, Callable, cast

from ..common.pagination import RowDict, RowList
from ..common.security import content_checksum
from ..db.fts import hybrid_search
from ..errors import AppError, ErrorCode
from .base import BaseRepository

REVIEW_PUBLISHED = "published"
REVIEW_REVOKED = "revoked"


class KnowledgeRepository(BaseRepository):
    table = "biz_knowledge_article"
    default_order = "updated_at"
    order_whitelist = {"id", "title", "category", "source_type", "view_count", "created_at", "updated_at"}
    searchable = ["title", "content", "summary", "tags"]

    FTS_TABLE = "biz_knowledge_fts"
    FTS_COLUMNS = ["title", "content", "tags"]

    def search(self, keyword: str, filters: dict[str, Any] | None = None,
               page_num: int = 1, page_size: int = 20) -> dict[str, Any]:
        """中文全文检索：长词走 FTS，短词走 instr 兜底。

        默认只召回 published：撤销的条目不再进入数字资产，但记录保留可追溯。
        """
        merged = dict(filters or {})
        merged.setdefault("review_status", REVIEW_PUBLISHED)

        # hybrid_search 会把 extra_where 包成 AND (...)，这里不能自带前导 AND
        conditions: list[str] = []
        extra_params: tuple[Any, ...] = ()
        for key, value in merged.items():
            if value in (None, ""):
                continue
            if isinstance(value, (list, tuple, set)):
                values = [v for v in value if v not in (None, "")]
                if not values:
                    continue
                conditions.append(f"{key} IN ({', '.join('?' for _ in values)})")
                extra_params += tuple(values)
            else:
                conditions.append(f"{key} = ?")
                extra_params += (value,)
        extra_where = " AND ".join(conditions)

        result = hybrid_search(
            self.conn,
            fts_table=self.FTS_TABLE,
            content_table=self.table,
            keyword=keyword,
            columns=self.FTS_COLUMNS,
            extra_where=extra_where.strip(),
            extra_params=extra_params,
            page_num=page_num,
            page_size=page_size,
        )
        return result

    def increment_views(self, pk: int) -> None:
        self.conn.execute(
            "UPDATE biz_knowledge_article SET view_count = view_count + 1 WHERE id = ?", (pk,)
        )

    def top_tags(self, limit: int = 20) -> RowList:
        rows = self.conn.execute(
            "SELECT tags, COUNT(*) AS c FROM biz_knowledge_article"
            " WHERE is_deleted = 0 AND review_status = 'published'"
            " AND tags IS NOT NULL AND tags <> ''"
            " GROUP BY tags ORDER BY c DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 计数 ----
    def published_count(self, project_id: int | None = None) -> int:
        """已发布知识数；给了 project_id 就只数本项目，项目视图不吃全库。"""
        sql = (
            "SELECT COUNT(*) FROM biz_knowledge_article"
            " WHERE is_deleted = 0 AND review_status = 'published'"
        )
        params: tuple[Any, ...] = ()
        if project_id:
            sql += " AND project_id = ?"
            params = (int(project_id),)
        return int(self.conn.execute(sql, params).fetchone()[0])

    def count_by_status(self, review_status: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM biz_knowledge_article WHERE is_deleted = 0 AND review_status = ?",
            (review_status,),
        ).fetchone()[0])

    # ---- 撤销 / 删除 ----
    def revoke(self, pk: int, operator: str = "") -> dict[str, Any]:
        """撤销正式条目。被下游引用时禁止撤销。"""
        article = self.get(pk)
        if not article:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
        if article.get("review_status") != REVIEW_PUBLISHED:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有已发布的条目可以撤销")

        from .knowledge_ref_repo import KnowledgeRefRepository

        KnowledgeRefRepository(self.conn).ensure_not_referenced(pk, action="撤销")

        self.update(pk, {"review_status": REVIEW_REVOKED, "status": REVIEW_REVOKED})
        return {"id": pk, "reviewStatus": REVIEW_REVOKED, "operator": operator}

    def restore(self, pk: int) -> dict[str, Any]:
        """恢复被撤销的条目。"""
        article = self.get(pk)
        if not article:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
        if article.get("review_status") != REVIEW_REVOKED:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有已撤销的条目可以恢复")
        self.update(pk, {"review_status": REVIEW_PUBLISHED, "status": REVIEW_PUBLISHED})
        return {"id": pk, "reviewStatus": REVIEW_PUBLISHED}

    def delete_article(self, pk: int) -> bool:
        """删除正式条目。被下游引用时禁止删除。"""
        article = self.get(pk)
        if not article:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")

        from .knowledge_ref_repo import KnowledgeRefRepository

        KnowledgeRefRepository(self.conn).ensure_not_referenced(pk, action="删除")
        return self.soft_delete(pk)

    # ---- 正文 ----
    def update_content(self, pk: int, title: str, content: str, **fields: Any) -> bool:
        payload = {
            "title": title,
            "content": content,
            "content_checksum": content_checksum(content),
        }
        for key in ("summary", "category", "tags", "module_id"):
            if key in fields:
                payload[key] = fields[key]
        return self.update(pk, payload)
