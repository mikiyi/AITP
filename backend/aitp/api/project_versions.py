"""项目版本 API。

一个项目下可建多套版本（v1.0 / v2.0），各测试资产通过 version_id 关联到
具体版本。版本挂在项目下管理，列表支持按项目筛选。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from ..common.enums import PROJECT_VERSION_STATUS
from ..common.validators import check_choice, optional_int, optional_str, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..repositories.project_version_repo import ProjectVersionRepository
from ..services import audit_service
from .helpers import (
    audit,
    body,
    commit,
    db,
    guard_project_scope,
    path_int,
    query_filters,
    require_project,
    scope_arg,
)

bp = Blueprint("project_versions", __name__)

FILTERS = {"projectId": "project_id", "status": "status"}

#: 版本状态中文名，供前端下拉
VERSION_STATUS_OPTIONS = [
    {"value": "planning", "label": "规划中"},
    {"value": "in_progress", "label": "进行中"},
    {"value": "released", "label": "已发布"},
]


def _repo(conn: Any) -> ProjectVersionRepository:
    return ProjectVersionRepository(conn)


def _to_public(row: dict[str, Any], asset_count: int | None = None) -> dict[str, Any]:
    data = {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "versionCode": row.get("version_code"),
        "name": row.get("name"),
        "description": row.get("description"),
        "status": row.get("status"),
        "sortOrder": row.get("sort_order"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
    if asset_count is not None:
        data["assetCount"] = asset_count
    return data


@bp.get("")
def list_versions() -> Response:
    conn = db()
    repo = _repo(conn)
    filters = query_filters(FILTERS)
    project_id = filters.get("project_id")
    if not project_id:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 projectId 不能为空")
    rows = repo.list_by_project(int(project_id))
    return ok({
        "list": [
            _to_public(row, repo.count_assets(int(row["id"]), project_id))
            for row in rows
        ],
        "statusOptions": VERSION_STATUS_OPTIONS,
    })


@bp.get("/<pk>")
def get_version(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目版本不存在")
    guard_project_scope(row, scope_arg(), "项目版本")
    return ok(_to_public(row, repo.count_assets(target, row.get("project_id"))))


@bp.post("")
def create_version() -> Response:
    conn = db()
    data = body()
    project_id = optional_int(data.get("projectId"), "projectId")
    if not project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    require_project(conn, project_id)
    version_code = require_str(data.get("versionCode"), "versionCode", 60)
    repo = _repo(conn)
    if repo.code_exists(project_id, version_code):
        raise AppError(ErrorCode.UNIQUE_CONFLICT, f"该版本号已存在：{version_code}")
    status = check_choice(data.get("status"), PROJECT_VERSION_STATUS, "status", "planning") or "planning"
    new_id = repo.insert({
        "project_id": project_id,
        "version_code": version_code,
        "name": optional_str(data.get("name"), "name", 200),
        "description": optional_str(data.get("description"), "description", 500),
        "status": status,
        "sort_order": optional_int(data.get("sortOrder"), "sortOrder") or 0,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_PROJECT_VERSION,
          new_id, version_code, {"projectId": project_id})
    return ok(_to_public(repo.get_by_id(new_id) or {}, 0))


@bp.put("/<pk>")
def update_version(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目版本不存在")
    data = body()
    payload: dict[str, Any] = {}
    if "versionCode" in data:
        payload["version_code"] = require_str(data["versionCode"], "versionCode", 60)
        if repo.code_exists(int(row["project_id"]), payload["version_code"], exclude_id=target):
            raise AppError(ErrorCode.UNIQUE_CONFLICT, f"该版本号已存在：{payload['version_code']}")
    if "name" in data:
        payload["name"] = optional_str(data["name"], "name", 200)
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 500)
    if "status" in data:
        payload["status"] = check_choice(data["status"], PROJECT_VERSION_STATUS, "status", "planning")
    if "sortOrder" in data:
        payload["sort_order"] = optional_int(data["sortOrder"], "sortOrder") or 0
    if payload:
        repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_PROJECT_VERSION,
          target, str(payload.get("version_code", row.get("version_code", "")) or ""),
          {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get_by_id(target) or {},
                         repo.count_assets(target, row.get("project_id"))))


@bp.delete("/<pk>")
def delete_version(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目版本不存在")
    asset_count = repo.count_assets(target, row.get("project_id"))
    if asset_count:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"该版本下仍有 {asset_count} 个资产或执行记录（需求/用例/缺陷/报告/脚本/计划/轮次等），"
            "请先迁移或删除后再删版本",
        )
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_PROJECT_VERSION,
          target, str(row.get("version_code") or ""), {"assetCount": asset_count})
    return ok({"id": target, "deleted": True})
