"""通用实体关联服务。

需求、用例、缺陷、模块、知识之间本来各有各的关联表，
这里再给一张通用关联表：任何两种实体都能挂上关系，
前端「关联页」按来源实体拉出一张关系网，也能反查。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.base import BaseRepository
from ..repositories.bug_repo import BugRepository
from ..repositories.knowledge_repo import KnowledgeRepository
from ..repositories.module_repo import ModuleRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.regression_repo import RegressionRepository
from ..repositories.relation_repo import (
    ENTITY_TYPES,
    RELATION_TYPES,
    EntityRelationRepository,
)
from ..repositories.report_repo import ReportRepository
from ..repositories.requirement_repo import RequirementRepository
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.ai_record_repo import AIRecordRepository


def _entity_exists(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> bool:
    """校验被关联的实体真实存在（软删的不算），避免挂出指向空的关系网。"""
    repos: dict[str, type[BaseRepository]] = {
        "requirement": RequirementRepository,
        "module": ModuleRepository,
        "case": TestCaseRepository,
        "bug": BugRepository,
        "knowledge": KnowledgeRepository,
        "report": ReportRepository,
        "regression": RegressionRepository,
        "ai_record": AIRecordRepository,
    }
    repo_cls = repos.get(entity_type)
    if repo_cls is None:
        return False
    return repo_cls(conn).get(int(entity_id)) is not None


def _check_entity_type(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if text not in ENTITY_TYPES:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 只能是：{'、'.join(ENTITY_TYPES)}")
    return text


def _check_relation_type(value: Any) -> str:
    text = str(value or "").strip()
    if text not in RELATION_TYPES:
        raise AppError(ErrorCode.PARAM_ERROR, f"关系类型只能是：{'、'.join(RELATION_TYPES)}")
    return text


def create_relation(
    conn: sqlite3.Connection,
    *,
    source_type: str,
    source_id: int,
    relation_type: str,
    target_type: str,
    target_id: int,
    remark: str = "",
) -> dict[str, Any]:
    repo = EntityRelationRepository(conn)
    source = _check_entity_type(source_type, "sourceType")
    target = _check_entity_type(target_type, "targetType")
    relation = _check_relation_type(relation_type)
    for role, e_type, e_id in (("source", source, source_id), ("target", target, target_id)):
        if not _entity_exists(conn, e_type, int(e_id)):
            raise AppError(
                ErrorCode.RESOURCE_NOT_FOUND,
                f"{role} 实体不存在：{e_type} #{e_id}",
            )
    if source == target and int(source_id) == int(target_id):
        raise AppError(ErrorCode.PARAM_ERROR, "不能把实体关联到它自己")
    if repo.exists(source, int(source_id), relation, target, int(target_id)):
        raise AppError(ErrorCode.UNIQUE_CONFLICT, "这条关联已经存在")
    new_id = repo.insert({
        "source_type": source,
        "source_id": int(source_id),
        "relation_type": relation,
        "target_type": target,
        "target_id": int(target_id),
        "remark": str(remark or ""),
    })
    row = repo.get(new_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "关联不存在")
    return row


def delete_relation(conn: sqlite3.Connection, pk: int) -> dict[str, Any]:
    repo = EntityRelationRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "关联不存在")
    repo.soft_delete(pk)
    return {"id": pk, "deleted": True}


def related_of(conn: sqlite3.Connection, entity_type: str, entity_id: int) -> list[dict[str, Any]]:
    """取某个实体关联到的所有实体（双向展开，带方向）。"""
    _check_entity_type(entity_type, "entityType")
    repo = EntityRelationRepository(conn)
    rows = repo.list_related(entity_type, entity_id)
    result: list[dict[str, Any]] = []
    for row in rows:
        outgoing = (
            str(row.get("source_type")) == entity_type
            and int(row.get("source_id") or 0) == int(entity_id)
        )
        result.append({
            "id": row.get("id"),
            "relationType": row.get("relation_type"),
            "direction": "out" if outgoing else "in",
            "sourceType": row.get("source_type"),
            "sourceId": row.get("source_id"),
            "targetType": row.get("target_type"),
            "targetId": row.get("target_id"),
            "peerType": row.get("target_type") if outgoing else row.get("source_type"),
            "peerId": row.get("target_id") if outgoing else row.get("source_id"),
            "remark": row.get("remark"),
            "createdAt": row.get("created_at"),
        })
    return result
