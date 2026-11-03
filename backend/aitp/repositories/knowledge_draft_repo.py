"""知识待审池仓储：AI 产出物先进池，审核通过才转正式库成为数字资产。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict, RowList
from ..common.security import content_checksum
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from .base import BaseRepository

DRAFT_STATUS_PENDING = "pending"
DRAFT_STATUS_APPROVED = "approved"
DRAFT_STATUS_REJECTED = "rejected"

SOURCE_TYPES = ("ai_output", "manual", "session", "case", "bug", "report")


class KnowledgeDraftRepository(BaseRepository):
    table = "biz_knowledge_draft"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "title", "category", "source_type", "review_status", "created_at", "updated_at"}
    searchable = ["title", "content", "tags", "producer"]

    # ---- 写 ----
    def create_draft(self, title: str, content: str, *, summary: str = "",
                     category: str = "", tags: str = "", module_id: int | None = None,
                     source_type: str = "ai_output", ref_type: str = "", ref_id: int | None = None,
                     ref_label: str = "", producer: str = "") -> int:
        if source_type not in SOURCE_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"来源类型非法：{source_type}")
        return self.insert({
            "title": title,
            "content": content,
            "summary": summary,
            "category": category,
            "tags": tags,
            "module_id": module_id,
            "source_type": source_type,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "ref_label": ref_label,
            "producer": producer,
            "review_status": DRAFT_STATUS_PENDING,
        })

    def review(self, draft_id: int, action: str, reviewer: str = "",
               review_comment: str = "") -> dict[str, Any]:
        """审核待审池条目。approve → 转正式库；reject → 标记驳回。"""
        draft = self.get(draft_id)
        if not draft:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "待审条目不存在")
        if draft["review_status"] != DRAFT_STATUS_PENDING:
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                f"该条目已审核（当前状态 {draft['review_status']}），无法重复操作",
            )

        if action == "approve":
            article_id = self._promote(draft, reviewer=reviewer, review_comment=review_comment)
            return {"draftId": draft_id, "articleId": article_id, "action": action}
        if action == "reject":
            now = utcnow_iso()
            self.update(draft_id, {
                "review_status": DRAFT_STATUS_REJECTED,
                "reviewer": reviewer,
                "review_comment": review_comment,
                "reviewed_at": now,
            })
            return {"draftId": draft_id, "articleId": None, "action": action}
        raise AppError(ErrorCode.PARAM_ERROR, "审核动作非法，只能是 approve 或 reject")

    def batch_review(self, draft_ids: list[int], action: str, reviewer: str = "",
                     review_comment: str = "") -> dict[str, Any]:
        succeeded: list[int] = []
        failed: list[dict[str, Any]] = []
        for draft_id in draft_ids:
            try:
                result = self.review(draft_id, action, reviewer=reviewer, review_comment=review_comment)
                succeeded.append(result["articleId"] or draft_id)
            except AppError as exc:
                failed.append({"draftId": draft_id, "code": exc.code, "message": exc.message})
        return {
            "action": action,
            "succeeded": succeeded,
            "failed": failed,
            "successCount": len(succeeded),
            "failCount": len(failed),
        }

    def update_draft(self, draft_id: int, data: dict[str, Any]) -> bool:
        allowed = {"title", "content", "summary", "category", "tags", "module_id"}
        payload = {k: v for k, v in data.items() if k in allowed}
        if not payload:
            return False
        return self.update(draft_id, payload)

    # ---- 读 ----
    def list_drafts(self, review_status: str = DRAFT_STATUS_PENDING, *,
                    page_num: int = 1, page_size: int = 20,
                    filters: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = dict(filters or {})
        if review_status:
            merged["review_status"] = review_status
        page = self.list(merged, None, page_num=page_num, page_size=page_size)
        return page.as_dict()

    def pending_count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM biz_knowledge_draft WHERE review_status = ? AND is_deleted = 0",
            (DRAFT_STATUS_PENDING,),
        ).fetchone()[0])

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT review_status, COUNT(*) AS c FROM biz_knowledge_draft WHERE is_deleted = 0"
            " GROUP BY review_status"
        ).fetchall()
        result = {DRAFT_STATUS_PENDING: 0, DRAFT_STATUS_APPROVED: 0, DRAFT_STATUS_REJECTED: 0}
        for row in rows:
            result[row["review_status"]] = int(row["c"])
        return result

    # ---- 内部 ----
    def _promote(self, draft: RowDict, reviewer: str = "", review_comment: str = "") -> int:
        """待审 → 正式库：写 article + v1 版本快照，并标记草稿已审核。"""
        from .knowledge_repo import KnowledgeRepository
        from .knowledge_version_repo import (
            KnowledgeVersionRepository,
            VERSION_STATUS_PUBLISHED,
        )

        now = utcnow_iso()
        article_repo = KnowledgeRepository(self.conn)
        payload = {
            "title": draft["title"],
            "content": draft["content"],
            "summary": draft["summary"] or "",
            "category": draft["category"] or "general",
            "tags": draft["tags"] or "",
            "source_type": draft["source_type"] or "ai_output",
            "module_id": draft.get("module_id"),
            "ref_type": draft.get("ref_type") or "",
            "ref_id": draft.get("ref_id"),
            "review_status": "published",
            "current_version": 1,
            "draft_version": 1,
            "content_checksum": content_checksum(draft["content"] or ""),
        }
        if draft.get("ref_type") == "task" and draft.get("ref_id"):
            if self._row_exists("biz_ai_task", draft["ref_id"]):
                payload["ai_task_id"] = draft["ref_id"]
        if draft.get("ref_type") == "session" and draft.get("ref_id"):
            if self._row_exists("biz_ai_session", draft["ref_id"]):
                payload["session_id"] = draft["ref_id"]

        article_id = article_repo.insert(payload)

        KnowledgeVersionRepository(self.conn).create_version(
            article_id,
            title=draft["title"],
            content=draft["content"],
            summary=draft["summary"] or "",
            category=draft["category"] or "general",
            tags=draft["tags"] or "",
            change_log="由待审池审核通过转正",
            created_by=reviewer,
            review_status=VERSION_STATUS_PUBLISHED,
            reviewer=reviewer,
            review_comment=review_comment,
        )

        self.update(draft["id"], {
            "review_status": DRAFT_STATUS_APPROVED,
            "reviewer": reviewer,
            "review_comment": review_comment,
            "reviewed_at": now,
            "article_id": article_id,
        })
        return article_id

    def _row_exists(self, table: str, row_id: int) -> bool:
        return self.conn.execute(
            f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)
        ).fetchone() is not None
