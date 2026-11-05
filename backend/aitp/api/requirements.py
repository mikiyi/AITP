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
from ..services import audit_service, tabular_export
from .helpers import (
    audit,
    batch_soft_delete,
    body,
    commit,
    db,
    download_response,
    ids_of,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    tabular_format_of,
)

bp = Blueprint("requirements", __name__)

FILTERS = {"projectId": "project_id", "status": "status", "priority": "priority", "versionId": "version_id"}
WRITE_FIELDS = {"title", "content", "source", "version", "status", "priority", "tags", "project_id", "version_id"}

REQUIREMENT_TABULAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reqNo", "需求编号"),
    ("title", "标题"),
    ("status", "状态"),
    ("priority", "优先级"),
    ("version", "版本"),
    ("source", "来源"),
    ("tags", "标签"),
    ("content", "正文"),
    ("createdAt", "创建时间"),
    ("updatedAt", "更新时间"),
)


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


@bp.get("/export")
def export_requirements() -> Response:
    """导出需求清单为 CSV / JSON，筛选条件与列表接口一致。"""
    conn = db()
    fmt = tabular_format_of(request.args.get("format"))
    filters = query_filters(FILTERS)
    rows = RequirementRepository(conn).list_all(filters, keyword_of() or None, limit=10000)
    content, mimetype, extension = tabular_export.render(
        fmt, REQUIREMENT_TABULAR_COLUMNS, [_to_public(row) for row in rows]
    )
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_REQUIREMENT, None, "",
          {"format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename("需求清单", extension), mimetype, count=len(rows)
    )


@bp.get("/<pk>")
def get_requirement(pk: str) -> Response:
    conn = db()
    row = RequirementRepository(conn).get(path_int(pk))
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
    target = path_int(pk)
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
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "需求不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_REQUIREMENT, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/batch-delete")
def batch_delete_requirements() -> Response:
    """批量删除需求；已删除或不存在的 id 归入 skipped。"""
    conn = db()
    ids = ids_of(body())
    return ok(batch_soft_delete(
        conn, RequirementRepository(conn), ids, audit_service.RESOURCE_REQUIREMENT
    ))


def request_int(name: str) -> int | None:
    raw = request.args.get(name)
    if raw in (None, ""):
        return None
    return require_int(raw, name)
