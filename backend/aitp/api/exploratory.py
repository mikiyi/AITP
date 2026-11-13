"""Web 探索性测试 API（三期 GAP-45）。

配置 → 建会话（给定目标地址与探索计划）→ 投递任务驱动 MCP 浏览器 →
逐步留痕与异常证据 → 产物进待审池（审核走 /knowledge/drafts）。
产物不直接写缺陷库；审核通过才转 biz_bug。
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.exploration_repo import ExplorationSessionRepository
from ..services import audit_service, exploratory_service
from .helpers import (
    audit, body, commit, db, guard_project_scope, keyword_of,
    page_args, path_int, query_filters, require_project, scope_arg,
)

bp = Blueprint("exploratory", __name__)


@bp.get("/meta")
def meta_api() -> Response:
    return ok({
        "drivers": [
            {"value": code, "label": exploratory_service.DRIVER_LABELS[code]}
            for code in exploratory_service.DRIVERS
        ],
        "statuses": [
            {"value": code, "label": exploratory_service.SESSION_STATUS_LABELS[code]}
            for code in exploratory_service.SESSION_STATUSES
        ],
        "anomalyTypes": [
            {"value": code, "label": label}
            for code, label in exploratory_service.ANOMALY_LABELS.items()
        ],
        "pinnedTools": list(exploratory_service.PINNED_TOOLS),
        "defaults": exploratory_service.DEFAULT_SETTINGS,
    })


@bp.get("/settings")
def get_settings_api() -> Response:
    conn = db()
    return ok(exploratory_service.get_settings(conn))


@bp.put("/settings")
def update_settings_api() -> Response:
    conn = db()
    payload = exploratory_service.update_settings(conn, body())
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, "exploratory_settings", 0,
          "Web 探索性测试配置", {"maxSteps": payload["maxSteps"], "mcpCommand": bool(payload["mcpCommand"])})
    return ok(payload)


@bp.get("/sessions")
def list_sessions_api() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters({"projectId": "project_id", "status": "status", "driver": "driver"})
    keyword = keyword_of()
    if keyword:
        filters["keyword"] = keyword
    return ok(exploratory_service.list_sessions(conn, filters, page_num, page_size))


@bp.post("/sessions")
def create_session_api() -> Response:
    conn = db()
    data = body()
    require_project(conn, int(data.get("projectId") or 0))
    session = exploratory_service.create_session(conn, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, "exploration_session", int(session["id"]),
          str(session["sessionNo"]), {"targetUrl": session["targetUrl"], "driver": session["driver"]})
    return ok(session)


@bp.get("/sessions/<pk>")
def get_session_api(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = ExplorationSessionRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    guard_project_scope(row, scope_arg(), "探索会话")
    return ok(exploratory_service.session_detail(conn, target))


@bp.delete("/sessions/<pk>")
def delete_session_api(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = ExplorationSessionRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    guard_project_scope(row, scope_arg(), "探索会话")
    exploratory_service.delete_session(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, "exploration_session", target,
          str(row.get("session_no") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/sessions/<pk>/run")
def run_session_api(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = ExplorationSessionRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    guard_project_scope(row, scope_arg(), "探索会话")
    session = exploratory_service.run_session(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, "exploration_session", target,
          str(session["sessionNo"]), {"taskId": session["taskId"]})
    return ok(session)


@bp.get("/steps/<pk>/evidence")
def step_evidence_api(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    evidence = exploratory_service.step_evidence(conn, target)
    if evidence is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "该步骤没有证据快照")
    return ok(evidence)