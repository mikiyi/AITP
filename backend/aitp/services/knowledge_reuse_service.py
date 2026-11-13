"""知识条目跨项目复用（GAP-42）。

全局知识 = project_id 为空的知识条目。项目知识提升为全局时保留 source_project_id；
其他项目使用时只在 biz_knowledge_ref 登记 ref_type='project'，不复制正文。
引用一旦存在，撤销/删除均被现有引用保护拦截。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.knowledge_ref_repo import KnowledgeRefRepository
from ..repositories.knowledge_repo import KnowledgeRepository


def _project_name(conn: sqlite3.Connection, project_id: int) -> str:
    row = conn.execute(
        "SELECT name FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    return str(row["name"] or "") if row else ""


def project_refs(conn: sqlite3.Connection, article_id: int) -> list[dict[str, Any]]:
    return [
        ref for ref in KnowledgeRefRepository(conn).list_refs(article_id)
        if ref.get("refType") == "project"
    ]


def source_project_name(conn: sqlite3.Connection, source_project_id: Any) -> str:
    if not source_project_id:
        return ""
    return _project_name(conn, int(source_project_id))


def promote_to_global(conn: sqlite3.Connection, article_id: int, operator: str = "") -> dict[str, Any]:
    repo = KnowledgeRepository(conn)
    row = repo.get(article_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    if row.get("project_id") is None:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该条目已是全局知识，无需重复提升")
    if row.get("review_status") != "published":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有已发布的条目可以提升为全局知识")
    source_project_id = int(row["project_id"])
    repo.update(article_id, {
        "project_id": None,
        "source_project_id": source_project_id,
        # 功能点属于原项目模块树，提升为全局后不再挂模块，避免跨项目错挂
        "module_id": None,
        "promoted_at": utcnow_iso(),
        "promoted_by": operator,
    })
    return {
        "articleId": article_id,
        "sourceProjectId": source_project_id,
        "sourceProjectName": _project_name(conn, source_project_id),
        "promotedAt": utcnow_iso(),
        "promotedBy": operator,
    }


def reference_project(conn: sqlite3.Connection, article_id: int, project_id: int,
                      operator: str = "") -> list[dict[str, Any]]:
    """把全局知识登记为某项目的引用；非全局条目必须先提升。"""
    repo = KnowledgeRepository(conn)
    row = repo.get(article_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    project_row = conn.execute(
        "SELECT id FROM biz_project WHERE id = ? AND is_deleted = 0", (int(project_id),)
    ).fetchone()
    if not project_row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    if row.get("project_id") is not None:
        if int(row["project_id"]) == int(project_id):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "当前项目就是条目的归属项目，无需引用")
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            "跨项目引用前请先在来源项目把条目提升为全局知识",
        )
    if row.get("source_project_id") and int(row["source_project_id"]) == int(project_id):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "来源项目无需重复登记引用")
    KnowledgeRefRepository(conn).add_ref(
        article_id, "project", int(project_id), _project_name(conn, int(project_id))
    )
    return project_refs(conn, article_id)


def unreference_project(conn: sqlite3.Connection, article_id: int,
                        project_id: int) -> list[dict[str, Any]]:
    if not KnowledgeRepository(conn).get(article_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    KnowledgeRefRepository(conn).remove_ref(article_id, "project", int(project_id))
    return project_refs(conn, article_id)
