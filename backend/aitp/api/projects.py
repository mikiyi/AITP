"""项目 API。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from ..common.enums import PROJECT_STATUS
from ..common.validators import check_choice, optional_str, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..services import audit_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, path_int, query_filters

bp = Blueprint("projects", __name__)

FILTERS = {"status": "status", "owner": "owner"}
WRITE_FIELDS = {"code", "name", "description", "owner", "status"}


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "code": row.get("code"),
        "name": row.get("name"),
        "description": row.get("description"),
        "owner": row.get("owner"),
        "status": row.get("status"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@bp.get("")
def list_projects() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = ProjectRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_to_public(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/options")
def project_options() -> Response:
    conn = db()
    rows = ProjectRepository(conn).list_all({"status": "active"}, None, limit=500)
    return ok([{"id": r["id"], "code": r["code"], "name": r["name"]} for r in rows])


@bp.get("/<pk>")
def get_project(pk: str) -> Response:
    conn = db()
    row = ProjectRepository(conn).get(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    return ok(_to_public(row))


@bp.post("")
def create_project() -> Response:
    conn = db()
    data = body()
    code = require_str(data.get("code"), "code", 64)
    name = require_str(data.get("name"), "name", 200)
    repo = ProjectRepository(conn)
    if repo.code_exists(code):
        raise AppError(ErrorCode.UNIQUE_CONFLICT, f"项目编号已存在：{code}")
    status = check_choice(data.get("status"), PROJECT_STATUS, "status", "active") or "active"
    new_id = repo.insert({
        "code": code,
        "name": name,
        "description": optional_str(data.get("description"), "description"),
        "owner": optional_str(data.get("owner"), "owner"),
        "status": status,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_PROJECT, new_id, name,
          {"code": code})
    return ok(_to_public(ProjectRepository(conn).get(new_id) or {}))


@bp.put("/<pk>")
def update_project(pk: str) -> Response:
    conn = db()
    repo = ProjectRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    data = body()
    payload = {k: v for k, v in data.items() if k in WRITE_FIELDS}
    if "status" in payload:
        payload["status"] = check_choice(payload["status"], PROJECT_STATUS, "status", "active")
    if "code" in payload and payload["code"]:
        payload["code"] = require_str(payload["code"], "code", 64)
        if repo.code_exists(payload["code"], exclude_id=target):
            raise AppError(ErrorCode.UNIQUE_CONFLICT, f"项目编号已存在：{payload['code']}")
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_PROJECT, target,
          str(payload.get("name", row.get("name", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_project(pk: str) -> Response:
    conn = db()
    repo = ProjectRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_PROJECT, target,
          row.get("name", ""))
    return ok({"id": target, "deleted": True})
