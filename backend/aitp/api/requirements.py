"""需求 API。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.enums import REQUIREMENT_STATUS
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..repositories.requirement_repo import RequirementRepository
from ..services import audit_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, query_filters

bp = Blueprint("requirements", __name__)

FILTERS = {"projectId": "project_id", "status": "status", "priority": "priority"}
WRITE_FIELDS = {"title", "content", "source", "version", "status", "priority", "tags", "project_id"}


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "reqNo": row.get("req_no"),
        "title": row.get("title"),
        "content": row.get("content"),
        "source": row.get("source"),
        "version": row.get("version"),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "tags": row.get("tags"),
        "aiTaskId": row.get("ai_task_id"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@bp.get("")
def list_requirements() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = RequirementRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_to_public(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/options")
def requirement_options() -> Response:
    conn = db()
    repo = RequirementRepository(conn)
    project_id = request_int("projectId")
    rows = repo.list_by_project(project_id) if project_id else repo.list_all(None, None, limit=500)
    return ok([
        {"id": r["id"], "reqNo": r["req_no"], "title": r["title"], "projectId": r["project_id"]}
        for r in rows
    ])


@bp.get("/<pk>")
def get_requirement(pk: str) -> Response:
    conn = db()
    row = RequirementRepository(conn).get(int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "需求不存在")
    return ok(_to_public(row))


@bp.post("")
def create_requirement() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    status = check_choice(data.get("status"), REQUIREMENT_STATUS, "status", "draft") or "draft"
    repo = RequirementRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "req_no": repo.next_req_no(project_id),
        "title": title,
        "content": optional_str(data.get("content"), "content", 200000),
        "source": optional_str(data.get("source"), "source", 200),
        "version": optional_str(data.get("version"), "version", 50),
        "status": status,
        "priority": optional_str(data.get("priority"), "priority", 20),
        "tags": optional_str(data.get("tags"), "tags", 500),
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REQUIREMENT, new_id, title)
    return ok(_to_public(RequirementRepository(conn).get(new_id) or {}))


@bp.put("/<pk>")
def update_requirement(pk: str) -> Response:
    conn = db()
    repo = RequirementRepository(conn)
    target = int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "需求不存在")
    data = body()
    payload = {k: v for k, v in data.items() if k in WRITE_FIELDS}
    if "status" in payload:
        payload["status"] = check_choice(payload["status"], REQUIREMENT_STATUS, "status", row["status"])
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REQUIREMENT, target,
          str(payload.get("title", row.get("title", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_requirement(pk: str) -> Response:
    conn = db()
    repo = RequirementRepository(conn)
    target = int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "需求不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_REQUIREMENT, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


def request_int(name: str) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    return require_int(raw, name)
