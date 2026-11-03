"""知识版本仓储：每次编辑产生新版本，审核通过后才写回正式正文。"""

from __future__ import annotations

import difflib
from typing import Any

from ..common.pagination import RowDict, RowList
from ..common.security import content_checksum
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from .base import BaseRepository

VERSION_STATUS_PENDING = "pending"
VERSION_STATUS_PUBLISHED = "published"
VERSION_STATUS_REJECTED = "rejected"


class KnowledgeVersionRepository(BaseRepository):
    table = "biz_knowledge_version"
    soft_delete_enabled = False
    default_order = "version"
    default_order_dir = "desc"
    order_whitelist = {"id", "version", "review_status", "created_at", "updated_at"}

    # ---- 读 ----
    def list_versions(self, article_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT id, article_id, version, title, summary, category, tags, review_status,"
            " reviewer, review_comment, reviewed_at, change_log, checksum, created_by,"
            " created_at, updated_at, LENGTH(content) AS content_length"
            " FROM biz_knowledge_version WHERE article_id = ? ORDER BY version DESC",
            (article_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, article_id: int, version: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_knowledge_version WHERE article_id = ? AND version = ?",
            (article_id, version),
        ).fetchone()
        return dict(row) if row else None

    def next_version(self, article_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM biz_knowledge_version WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        return int(row["v"]) + 1

    def pending_versions(self, article_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_knowledge_version WHERE article_id = ? AND review_status = ?"
            " ORDER BY version DESC",
            (article_id, VERSION_STATUS_PENDING),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 写 ----
    def create_version(self, article_id: int, title: str, content: str, *,
                       summary: str = "", category: str = "", tags: str = "",
                       change_log: str = "", created_by: str = "",
                       review_status: str = VERSION_STATUS_PENDING,
                       reviewer: str = "", review_comment: str = "") -> int:
        version = self.next_version(article_id)
        new_id = self.insert({
            "article_id": article_id,
            "version": version,
            "title": title,
            "content": content,
            "summary": summary,
            "category": category,
            "tags": tags,
            "review_status": review_status,
            "reviewer": reviewer,
            "review_comment": review_comment,
            "reviewed_at": utcnow_iso() if review_status != VERSION_STATUS_PENDING else None,
            "change_log": change_log,
            "checksum": content_checksum(content),
            "created_by": created_by,
        })
        self.conn.execute(
            "UPDATE biz_knowledge_article SET draft_version = ?, updated_at = ? WHERE id = ?",
            (version, utcnow_iso(), article_id),
        )
        return new_id

    def apply_version(self, article_id: int, version: int, reviewer: str = "",
                      review_comment: str = "") -> dict[str, Any]:
        """审核通过新版本：内容写回 article，current_version 前移。"""
        snapshot = self.get_version(article_id, version)
        if not snapshot:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在")
        if snapshot["review_status"] != VERSION_STATUS_PENDING:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "该版本已审核，无需重复操作")

        now = utcnow_iso()
        self.conn.execute(
            "UPDATE biz_knowledge_article SET title = ?, content = ?, summary = ?, category = ?,"
            " tags = ?, content_checksum = ?, current_version = ?, draft_version = ?,"
            " review_status = 'published', updated_at = ? WHERE id = ?",
            (
                snapshot["title"],
                snapshot["content"],
                snapshot["summary"],
                snapshot["category"],
                snapshot["tags"],
                snapshot["checksum"] or content_checksum(snapshot["content"]),
                version,
                version,
                now,
                article_id,
            ),
        )
        self.conn.execute(
            "UPDATE biz_knowledge_version SET review_status = ?, reviewer = ?,"
            " review_comment = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (VERSION_STATUS_PUBLISHED, reviewer, review_comment, now, now, snapshot["id"]),
        )
        return self.get_version(article_id, version) or {}

    def reject_version(self, article_id: int, version: int, reviewer: str = "",
                       review_comment: str = "") -> dict[str, Any]:
        snapshot = self.get_version(article_id, version)
        if not snapshot:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在")
        if snapshot["review_status"] != VERSION_STATUS_PENDING:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "该版本已审核，无需重复操作")
        now = utcnow_iso()
        self.conn.execute(
            "UPDATE biz_knowledge_version SET review_status = ?, reviewer = ?,"
            " review_comment = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
            (VERSION_STATUS_REJECTED, reviewer, review_comment, now, now, snapshot["id"]),
        )
        # 驳回后草稿版本号回落到当前生效版本
        self.conn.execute(
            "UPDATE biz_knowledge_article SET draft_version = current_version, updated_at = ?"
            " WHERE id = ?",
            (now, article_id),
        )
        return self.get_version(article_id, version) or {}

    # ---- diff ----
    def diff(self, article_id: int, from_version: int, to_version: int) -> dict[str, Any]:
        left = self.get_version(article_id, from_version)
        right = self.get_version(article_id, to_version)
        if not left:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"版本 {from_version} 不存在")
        if not right:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"版本 {to_version} 不存在")
        diff_lines = list(difflib.unified_diff(
            (left["content"] or "").splitlines(),
            (right["content"] or "").splitlines(),
            fromfile=f"v{from_version}",
            tofile=f"v{to_version}",
            lineterm="",
        ))
        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        return {
            "from": from_version,
            "to": to_version,
            "diff": diff_lines,
            "added": added,
            "removed": removed,
            "changed": added > 0 or removed > 0,
        }
