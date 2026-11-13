"""知识引用登记：被下游引用的条目禁止撤销 / 删除。"""

from __future__ import annotations

from ..common.pagination import RowList
from ..errors import AppError, ErrorCode
from .base import BaseRepository

REF_TYPES = ("report", "article", "task", "case", "bug", "module", "project")

_LABEL_SOURCES = {
    "report": ("biz_report", "title"),
    "article": ("biz_knowledge_article", "title"),
    "task": ("biz_ai_task", "task_type"),
    "case": ("biz_test_case", "title"),
    "bug": ("biz_bug", "title"),
    "module": ("biz_module", "name"),
    "project": ("biz_project", "name"),
}


class KnowledgeRefRepository(BaseRepository):
    table = "biz_knowledge_ref"
    soft_delete_enabled = False
    default_order = "id"
    default_order_dir = "asc"

    # ---- 写 ----
    def add_ref(self, article_id: int, ref_type: str, ref_id: int | None = None,
                ref_label: str = "") -> None:
        if ref_type not in REF_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"引用类型非法：{ref_type}")
        # ref_id 统一存 0 而非 NULL：唯一索引对 NULL 不去重
        normalized = int(ref_id or 0)
        existing = self.conn.execute(
            "SELECT id, ref_label FROM biz_knowledge_ref"
            " WHERE article_id = ? AND ref_type = ? AND ref_id = ?",
            (article_id, ref_type, normalized),
        ).fetchone()
        if existing:
            if ref_label and ref_label != existing["ref_label"]:
                self.update(existing["id"], {"ref_label": ref_label})
            return
        self.insert({
            "article_id": article_id,
            "ref_type": ref_type,
            "ref_id": normalized,
            "ref_label": ref_label or "",
        })

    def remove_ref(self, article_id: int, ref_type: str, ref_id: int | None = None) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM biz_knowledge_ref WHERE article_id = ? AND ref_type = ? AND ref_id = ?",
            (article_id, ref_type, int(ref_id or 0)),
        )
        return cursor.rowcount > 0

    def remove_refs_by_target(self, ref_type: str, ref_id: int) -> int:
        """下游对象被删除/解绑时同步清理引用登记。"""
        cursor = self.conn.execute(
            "DELETE FROM biz_knowledge_ref WHERE ref_type = ? AND ref_id = ?",
            (ref_type, int(ref_id or 0)),
        )
        return cursor.rowcount

    # ---- 读 ----
    def list_refs(self, article_id: int) -> RowList:
        """引用清单。字段按前端契约输出 camelCase。"""
        rows = self.conn.execute(
            "SELECT * FROM biz_knowledge_ref WHERE article_id = ? ORDER BY id ASC",
            (article_id,),
        ).fetchall()
        result: RowList = []
        for row in rows:
            item = dict(row)
            result.append({
                "id": int(item["id"]),
                "articleId": int(item["article_id"]),
                "refType": str(item["ref_type"]),
                "refId": int(item["ref_id"] or 0),
                "refLabel": str(item["ref_label"] or ""),
                "label": self._resolve_label(item["ref_type"], item["ref_id"], item["ref_label"]),
                "createdAt": item["created_at"],
                "updatedAt": item["updated_at"],
            })
        return result

    def count_by_article(self, article_id: int) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM biz_knowledge_ref WHERE article_id = ?", (article_id,)
        ).fetchone()[0])

    def referenced_articles(self, ref_type: str, ref_id: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT article_id FROM biz_knowledge_ref WHERE ref_type = ? AND ref_id = ?",
            (ref_type, int(ref_id or 0)),
        ).fetchall()
        return [int(r["article_id"]) for r in rows]

    # ---- 引用保护 ----
    def ensure_not_referenced(self, article_id: int, action: str = "撤销") -> None:
        refs = self.list_refs(article_id)
        if refs:
            raise AppError(
                ErrorCode.RESOURCE_REFERENCED,
                f"该知识条目已被 {len(refs)} 处引用，禁止{action}",
                {"refs": refs},
            )

    # ---- 内部 ----
    def _resolve_label(self, ref_type: str, ref_id: int | None, fallback: str) -> str:
        source = _LABEL_SOURCES.get(ref_type)
        if source and ref_id:
            table, column = source
            try:
                row = self.conn.execute(
                    f"SELECT {column} AS label FROM {table} WHERE id = ?", (ref_id,)
                ).fetchone()
            except Exception:  # noqa: BLE001 - 表结构差异不应阻断引用清单
                row = None
            if row and row["label"]:
                return str(row["label"])
        return fallback or f"{ref_type}#{ref_id}"
