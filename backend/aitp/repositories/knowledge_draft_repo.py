"""知识待审池仓储：AI 产出物先进池，审核通过才转正式库成为数字资产。"""

from __future__ import annotations

import re
from typing import Any

from ..common.pagination import RowDict, RowList
from ..common.security import content_checksum
from ..common.time_utils import local_stamp, utcnow_iso
from ..errors import AppError, ErrorCode
from .base import BaseRepository

DRAFT_STATUS_PENDING = "pending"
DRAFT_STATUS_APPROVED = "approved"
DRAFT_STATUS_REJECTED = "rejected"

SOURCE_ASSISTANT = "assistant"
SOURCE_EXPLORATORY = "exploratory"
SOURCE_TYPES = ("ai_output", "manual", "session", "case", "bug", "report", SOURCE_ASSISTANT, SOURCE_EXPLORATORY)

#: 来源枚举全中文：前端只消费 label，不在页面里复制一份映射
SOURCE_TYPE_LABELS = {
    "ai_output": "AI 产出",
    "manual": "手工录入",
    "session": "会话",
    "case": "测试用例",
    "bug": "缺陷",
    "report": "测试报告",
    SOURCE_ASSISTANT: "知识库问答助手",
    SOURCE_EXPLORATORY: "Web 探索性测试",
}

ASSISTANT_TITLE_PREFIX = "AI问答助手"
_TITLE_PREFIX_RE = re.compile(r"^AI问答助手\+\d{8,14}[｜|\s]*")


def source_type_label(source_type: str) -> str:
    return SOURCE_TYPE_LABELS.get(source_type, source_type or "-")


def assistant_title(title: str, answered_at: str, *, with_time: bool) -> str:
    """给问答记录拼来源前缀。

    派生标题必须可重复计算：先剥掉旧前缀再拼新的，
    否则反复审核会叠出「AI问答助手…AI问答助手…」。
    """
    base = _TITLE_PREFIX_RE.sub("", title or "").strip() or "问答记录"
    stamp = local_stamp(answered_at, with_time=with_time)
    return f"{ASSISTANT_TITLE_PREFIX}+{stamp}｜{base}"


class KnowledgeDraftRepository(BaseRepository):
    table = "biz_knowledge_draft"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "title", "category", "source_type", "review_status", "created_at", "updated_at"}
    searchable = ["title", "content", "tags", "producer"]

    # ---- 写 ----
    def create_draft(self, title: str, content: str, *, summary: str = "",
                     category: str = "", tags: str = "", module_id: int | None = None,
                     project_id: int | None = None,
                     source_type: str = "ai_output", ref_type: str = "", ref_id: int | None = None,
                     ref_label: str = "", producer: str = "",
                     answered_at: str = "", answer_mode: str = "", answer_model: str = "",
                     refs_json: str = "", review_required: bool = False,
                     content_hash: str = "") -> int:
        if source_type not in SOURCE_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"来源类型非法：{source_type}")
        return self.insert({
            "title": title,
            "content": content,
            "summary": summary,
            "category": category,
            "tags": tags,
            "module_id": module_id,
            "project_id": project_id,
            "source_type": source_type,
            "ref_type": ref_type,
            "ref_id": ref_id,
            "ref_label": ref_label,
            "producer": producer,
            "answered_at": answered_at or None,
            "answer_mode": answer_mode,
            "answer_model": answer_model,
            "refs_json": refs_json,
            "review_required": 1 if review_required else 0,
            "content_hash": content_hash or None,
            "review_status": DRAFT_STATUS_PENDING,
        })

    def find_by_content_hash(self, content_hash: str) -> RowDict | None:
        if not content_hash:
            return None
        row = self.conn.execute(
            "SELECT * FROM biz_knowledge_draft WHERE content_hash = ? AND is_deleted = 0"
            " ORDER BY id DESC LIMIT 1",
            (content_hash,),
        ).fetchone()
        return dict(row) if row else None

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
            if str(draft.get("source_type") or "") == SOURCE_EXPLORATORY:
                bug_id = self._promote_to_bug(draft, reviewer=reviewer, review_comment=review_comment)
                return {"draftId": draft_id, "articleId": None, "bugId": bug_id, "action": action}
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
        bug_ids: list[int] = []
        failed: list[dict[str, Any]] = []
        for draft_id in draft_ids:
            try:
                result = self.review(draft_id, action, reviewer=reviewer, review_comment=review_comment)
                # 探索性产物转缺陷（bugId），不能把草稿 id 混进文章 id 列表去同步知识文件
                if result.get("bugId"):
                    bug_ids.append(int(result["bugId"]))
                else:
                    succeeded.append(result["articleId"] or draft_id)
            except AppError as exc:
                failed.append({"draftId": draft_id, "code": exc.code, "message": exc.message})
        return {
            "action": action,
            "succeeded": succeeded,
            "bugIds": bug_ids,
            "failed": failed,
            "successCount": len(succeeded) + len(bug_ids),
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

    def pending_count(self, project_id: int | None = None) -> int:
        """待审条目数；给了 project_id 就只数本项目入池的产出。"""
        sql = "SELECT COUNT(*) FROM biz_knowledge_draft WHERE review_status = ? AND is_deleted = 0"
        params: list[Any] = [DRAFT_STATUS_PENDING]
        if project_id:
            sql += " AND project_id = ?"
            params.append(int(project_id))
        return int(self.conn.execute(sql, params).fetchone()[0])

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
        source_type = draft.get("source_type") or "ai_output"
        title = str(draft["title"] or "")
        if source_type == SOURCE_ASSISTANT:
            # 正式库标题按回答日期收敛到天：同一批问答排序稳定，来源细节仍由来源列与溯源字段承载
            title = assistant_title(title, str(draft.get("answered_at") or ""), with_time=False)
        payload = {
            "title": title,
            "content": draft["content"],
            "summary": draft["summary"] or "",
            "category": draft["category"] or "general",
            "tags": draft["tags"] or "",
            "source_type": source_type,
            "module_id": draft.get("module_id"),
            "project_id": draft.get("project_id"),
            "ref_type": draft.get("ref_type") or "",
            "ref_id": draft.get("ref_id"),
            "review_status": "published",
            "current_version": 1,
            "draft_version": 1,
            "content_checksum": content_checksum(draft["content"] or ""),
            "answered_at": draft.get("answered_at"),
            "answer_mode": draft.get("answer_mode") or "",
            "answer_model": draft.get("answer_model") or "",
            "refs_json": draft.get("refs_json") or "",
            "review_required": int(draft.get("review_required") or 0),
            "organize_status": "pending" if source_type == SOURCE_ASSISTANT else "",
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
            title=title,
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

    def _promote_to_bug(self, draft: RowDict, reviewer: str = "", review_comment: str = "") -> int:
        """探索性产物审核通过 → 转缺陷（GAP-45）。

        产物本体是「疑似缺陷」，审核通过才写 biz_bug；同时把异常步的证据挂到缺陷上。
        不写知识正式库——探索性结论的可复盘价值由证据包承载，避免污染知识库。
        """
        from ..services import execution_protocol
        from .bug_repo import BugRepository

        now = utcnow_iso()
        project_id = int(draft.get("project_id") or 0)
        if not project_id:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "探索性产物缺少项目归属，无法转缺陷")
        bug_repo = BugRepository(self.conn)
        title = str(draft.get("title") or "探索性测试发现")[:500]
        bug_id = bug_repo.insert({
            "project_id": project_id,
            "bug_no": bug_repo.next_bug_no(project_id),
            "title": title,
            "severity": "P2",
            "status": "open",
            "priority": "P2",
            "bug_type": "functional",
            "description": str(draft.get("content") or "")[:100000],
            "reporter": (reviewer or "探索性测试")[:64],
            "module": str(draft.get("ref_label") or "")[:200],
        })
        # 异常步证据（source_type=exploratory, source_row_id=step_id）挂到缺陷上
        step_id = int(draft.get("ref_id") or 0)
        if str(draft.get("ref_type") or "") == "exploration_step" and step_id:
            self.conn.execute(
                "UPDATE biz_execution_evidence SET bug_id = ?, updated_at = ?"
                " WHERE source_type = ? AND source_row_id = ? AND is_deleted = 0"
                " AND (bug_id IS NULL OR bug_id = ?)",
                (int(bug_id), now, execution_protocol.SOURCE_EXPLORATORY, step_id, int(bug_id)),
            )
        self.update(draft["id"], {
            "review_status": DRAFT_STATUS_APPROVED,
            "reviewer": reviewer,
            "review_comment": review_comment,
            "reviewed_at": now,
            "bug_id": int(bug_id),
        })
        return int(bug_id)

    def _row_exists(self, table: str, row_id: int) -> bool:
        return self.conn.execute(
            f"SELECT 1 FROM {table} WHERE id = ?", (row_id,)
        ).fetchone() is not None
