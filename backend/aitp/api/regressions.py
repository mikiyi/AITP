"""回归清单 API：清单 CRUD、明细维护、AI 生成与在线执行。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.regression_repo import (
    RegressionItemRepository,
    RegressionRepository,
    item_summary,
    regression_summary,
)
from ..services import audit_service, regression_service
from .helpers import (
    attach_version_codes,
    audit,
    body,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    require_project,
    scope_arg,
)

bp = Blueprint("regressions", __name__)

FILTERS = {
    "projectId": "project_id",
    "status": "status",
    "riskLevel": "risk_level",
    "source": "source",
    "version": "version",
    "versionId": "version_id",
}


@bp.get("")
def list_regressions() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = RegressionRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [regression_summary(row) for row in payload["list"]]
    attach_version_codes(conn, payload["list"])
    return ok(payload)


@bp.get("/<pk>")
def get_regression(pk: str) -> Response:
    conn = db()
    repo = RegressionRepository(conn)
    detail = repo.get_detail(path_int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    guard_project_scope(detail, scope_arg(), "回归清单")
    payload = regression_summary(detail)
    payload["items"] = [item_summary(row) for row in detail.get("items", [])]
    payload["stats"] = repo.stats_of(path_int(pk))
    attach_version_codes(conn, [payload])
    return ok(payload)


@bp.post("")
def create_regression() -> Response:
    conn = db()
    data = body()
    new_id = regression_service.create_regression(
        conn,
        project_id=require_project(conn, require_int(data.get("projectId"), "projectId")),
        title=require_str(data.get("title"), "title", 200),
        change_scope=str(data.get("changeScope") or ""),
        affected_modules=str(data.get("affectedModules") or ""),
        version=str(data.get("version") or ""),
        version_id=optional_int(data.get("versionId"), "versionId"),
        risk_level=str(data.get("riskLevel") or "low"),
        content=str(data.get("content") or ""),
        source=str(data.get("source") or "manual"),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REGRESSION,
          new_id, str(data.get("title") or ""), {})
    detail = RegressionRepository(conn).get_detail(new_id)
    payload = regression_summary(detail or {})
    attach_version_codes(conn, [payload])
    return ok(payload)


@bp.put("/<pk>")
def update_regression(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    data = body()
    detail = regression_service.update_regression(conn, target, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REGRESSION,
          target, str(detail.get("title") or ""), {"fields": sorted(data.keys())})
    return ok(regression_summary(detail))


@bp.delete("/<pk>")
def delete_regression(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = RegressionRepository(conn)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_REGRESSION,
          target, str(row.get("title") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/<pk>/generate")
def generate_items(pk: str) -> Response:
    """用「回归清单」能力生成条目。"""
    conn = db()
    data = body()
    target = path_int(pk)
    profile_id = optional_int(data.get("profileId"), "profileId")
    result = regression_service.generate_items(conn, target, profile_id=profile_id)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_REGRESSION,
          target, "", {"created": result.get("created")})
    return ok(result)


@bp.post("/<pk>/items")
def add_item(pk: str) -> Response:
    conn = db()
    data = body()
    target = path_int(pk)
    item = regression_service.add_item(conn, target, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REGRESSION,
          target, str(item.get("title") or ""), {"itemId": item.get("id")})
    return ok(item)


@bp.put("/<pk>/items/<item_id>")
def update_item(pk: str, item_id: str) -> Response:
    conn = db()
    data = body()
    target = path_int(pk)
    item = regression_service.update_item(conn, target, path_int(item_id, "itemId"), data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REGRESSION,
          target, str(item.get("title") or ""), {"itemId": path_int(item_id, "itemId")})
    return ok(item)


@bp.delete("/<pk>/items/<item_id>")
def delete_item(pk: str, item_id: str) -> Response:
    conn = db()
    target = path_int(pk)
    result = regression_service.delete_item(conn, target, path_int(item_id, "itemId"))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_REGRESSION,
          target, "", {"itemId": path_int(item_id, "itemId")})
    return ok(result)


@bp.post("/<pk>/execute")
def execute_items(pk: str) -> Response:
    """在线执行：批量回写每条回归点的执行结果。"""
    conn = db()
    data = body()
    results = data.get("results")
    if not isinstance(results, list) or not results:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 results 必须是非空数组")
    target = path_int(pk)
    payload = regression_service.execute_items(conn, target, results)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REGRESSION,
          target, "", {"updated": len(payload.get("updated") or [])})
    return ok(payload)


@bp.get("/<pk>/items")
def list_items(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if RegressionRepository(conn).get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    rows = RegressionItemRepository(conn).list_by_regression(target)
    return ok({"list": [item_summary(row) for row in rows]})
