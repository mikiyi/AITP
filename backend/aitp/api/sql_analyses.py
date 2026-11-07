"""SQL 分析资产 API。

    GET    /api/v1/sql-analyses          列表
    GET    /api/v1/sql-analyses/<pk>     详情
    POST   /api/v1/sql-analyses          新增
    PUT    /api/v1/sql-analyses/<pk>     修改
    DELETE /api/v1/sql-analyses/<pk>     删除（软删）
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..repositories.sql_analysis_repo import (
    RISK_LEVELS,
    SqlAnalysisRepository,
    decorate,
)
from ..services import audit_service
from .helpers import (
    attach_version_codes,
    audit,
    body,
    check_ref_project,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    reject_project_move,
    require_project,
    scope_arg,
)

bp = Blueprint("sql_analyses", __name__)

FILTERS = {"projectId": "project_id", "riskLevel": "risk_level", "source": "source", "versionId": "version_id"}

#: 可写字段（白名单）
_TITLE_MAX = 300
_SQL_MAX = 200000
_ANALYSIS_MAX = 200000


@bp.get("")
def list_sql_analyses() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = SqlAnalysisRepository(conn)
    filters = query_filters(FILTERS)
    # 「无风险」是本模块的合法取值，不能被 query_filters 的 none→NULL 通用别名吃掉
    if str(request.args.get("riskLevel") or "").strip().lower() == "none":
        filters["risk_level"] = "none"
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = attach_version_codes(conn, [decorate(row) for row in payload["list"]])
    return ok(payload)


@bp.get("/<pk>")
def get_sql_analysis(pk: str) -> Response:
    conn = db()
    row = SqlAnalysisRepository(conn).get(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "SQL 分析记录不存在")
    guard_project_scope(row, scope_arg(), "SQL 分析记录")
    return ok(decorate(row))


@bp.post("")
def create_sql_analysis() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    require_project(conn, project_id)
    title = require_str(data.get("title"), "title", _TITLE_MAX)
    module_id = check_ref_project(
        conn, "biz_module", data.get("moduleId"), project_id, "功能点", "moduleId"
    )
    version_id = check_ref_project(
        conn, "biz_project_version", data.get("versionId"), project_id, "项目版本", "versionId"
    )
    risk = check_choice(data.get("riskLevel"), RISK_LEVELS, "riskLevel", "none") or "none"
    repo = SqlAnalysisRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "module_id": module_id,
        "version_id": version_id,
        "sql_no": repo.next_sql_no(),
        "title": title,
        "sql_text": optional_str(data.get("sqlText"), "sqlText", _SQL_MAX),
        "analysis": optional_str(data.get("analysis"), "analysis", _ANALYSIS_MAX),
        "risk_level": risk,
        "source": optional_str(data.get("source"), "source", 40) or "manual",
        "tags": optional_str(data.get("tags"), "tags", 500),
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_SQL_ANALYSIS,
          new_id, title, {"riskLevel": risk})
    return ok(decorate(repo.get(new_id) or {}))


@bp.put("/<pk>")
def update_sql_analysis(pk: str) -> Response:
    conn = db()
    repo = SqlAnalysisRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "SQL 分析记录不存在")
    data = body()
    payload: dict[str, Any] = {}
    reject_project_move(data, row.get("project_id"), "SQL 分析记录")
    if "title" in data:
        payload["title"] = require_str(data.get("title"), "title", _TITLE_MAX)
    if "moduleId" in data:
        payload["module_id"] = check_ref_project(
            conn, "biz_module", data["moduleId"], row.get("project_id"), "功能点", "moduleId"
        )
    if "versionId" in data:
        payload["version_id"] = check_ref_project(
            conn, "biz_project_version", data["versionId"],
            row.get("project_id"), "项目版本", "versionId",
        )
    if "sqlText" in data:
        payload["sql_text"] = optional_str(data.get("sqlText"), "sqlText", _SQL_MAX)
    if "analysis" in data:
        payload["analysis"] = optional_str(data.get("analysis"), "analysis", _ANALYSIS_MAX)
    if "riskLevel" in data:
        payload["risk_level"] = (
            check_choice(data.get("riskLevel"), RISK_LEVELS, "riskLevel", "none") or "none"
        )
    if "tags" in data:
        payload["tags"] = optional_str(data.get("tags"), "tags", 500)
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_SQL_ANALYSIS,
          target, str(payload.get("title") or row.get("title") or ""),
          {"fields": sorted(payload.keys())})
    return ok(decorate(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_sql_analysis(pk: str) -> Response:
    conn = db()
    repo = SqlAnalysisRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "SQL 分析记录不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_SQL_ANALYSIS,
          target, str(row.get("title") or ""))
    return ok({"id": target, "deleted": True})
