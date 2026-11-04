"""通用实体关联 API。

任何两种实体之间都能挂关系；关联页既能按来源实体拉出关系网，也能全量检索。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.relation_repo import (
    ENTITY_TYPES,
    RELATION_TYPES,
    EntityRelationRepository,
    relation_summary,
)
from ..services import audit_service, relation_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload

bp = Blueprint("relations", __name__)


@bp.get("/types")
def list_types() -> Response:
    """可关联的实体类型与关系类型。"""
    return ok({"entityTypes": list(ENTITY_TYPES), "relationTypes": list(RELATION_TYPES)})


@bp.get("")
def list_relations() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EntityRelationRepository(conn)
    filters: dict[str, Any] = {}
    for key, column in (("sourceType", "source_type"), ("targetType", "target_type"),
                        ("relationType", "relation_type")):
        value = request.args.get(key)
        if value:
            filters[column] = value
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [relation_summary(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/of/<entity_type>/<int:entity_id>")
def related_of(entity_type: str, entity_id: int) -> Response:
    """取某个实体关联到的所有实体。"""
    conn = db()
    return ok({"list": relation_service.related_of(conn, entity_type, entity_id)})


@bp.post("")
def create_relation() -> Response:
    conn = db()
    data = body()
    row = relation_service.create_relation(
        conn,
        source_type=str(data.get("sourceType") or ""),
        source_id=require_int(data.get("sourceId"), "sourceId"),
        relation_type=str(data.get("relationType") or ""),
        target_type=str(data.get("targetType") or ""),
        target_id=require_int(data.get("targetId"), "targetId"),
        remark=str(data.get("remark") or ""),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_ENTITY_RELATION,
          int(row.get("id") or 0), "", {
              "sourceType": row.get("source_type"),
              "sourceId": row.get("source_id"),
              "targetType": row.get("target_type"),
              "targetId": row.get("target_id"),
          })
    return ok(relation_summary(row))


@bp.delete("/<pk>")
def delete_relation(pk: str) -> Response:
    conn = db()
    result = relation_service.delete_relation(conn, int(pk))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_ENTITY_RELATION,
          int(pk), "", {})
    return ok(result)
