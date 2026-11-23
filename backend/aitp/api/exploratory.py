"""Web 探索性测试 API（三期 GAP-45）。

配置 → 建会话（给定目标地址与探索计划）→ 投递任务驱动 MCP 浏览器 →
逐步留痕与异常证据 → 产物进待审池（审核走 /knowledge/drafts）。
产物不直接写缺陷库；审核通过才转 biz_bug。
"""

from __future__ import annotations

from flask import Blueprint, Response, request, send_file

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.exploration_repo import ExplorationSessionRepository, ExplorationStepRepository
from ..repositories.automation_repo import AutomationScriptRepository
from ..services import audit_service, exploratory_service, media_service
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
        "suggestedMcpCommand": exploratory_service.suggested_mcp_command(),
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


@bp.post("/sessions/<pk>/to-script")
def session_to_script(pk: str) -> Response:
    """P1 #8 录制回放：把探索会话步骤转成 Playwright 回放脚本（复用探索记录器，不另造）。"""
    conn = db()
    target = path_int(pk)
    session = ExplorationSessionRepository(conn).get(target)
    if session is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    guard_project_scope(session, scope_arg(), "探索会话")
    steps = ExplorationStepRepository(conn).list_by_session(target)
    lines = [
        "import os, json",
        "from playwright.sync_api import sync_playwright",
        "",
        "ELEMENTS = {e['name']: e['selector'] for e in json.loads(os.environ.get('AITP_ELEMENTS_JSON', '[]'))}",
        "",
        "with sync_playwright() as p:",
        "    browser = p.chromium.launch(headless=False)",
        "    page = browser.new_page()",
    ]
    for s in steps:
        action = str(s.get("action") or "")
        selector = str(s.get("selector") or "")
        if action == "navigate":
            url = str(s.get("page_url") or session.get("target_url") or "")
            lines.append(f"    page.goto({url!r})")
        elif action == "click" and selector:
            lines.append(f"    page.click({selector!r})")
        elif action == "type" and selector:
            lines.append(f"    page.fill({selector!r}, {str(s.get('input_masked') or '')!r})")
        else:
            lines.append("    page.wait_for_timeout(500)")
    lines.append("    browser.close()")
    content = "\n".join(lines) + "\n"
    repo = AutomationScriptRepository(conn)
    script_no = repo.next_script_no("web")
    name = f"{session.get('name') or '探索'} 回放"
    new_id = repo.insert({
        "script_no": script_no, "project_id": session.get("project_id"),
        "name": name, "category": "web", "content": content,
        "source": "exploratory", "file_name": f"{script_no}.py",
        "run_mode": "script", "framework": "playwright", "status": "ready",
    })
    commit(conn)
    return ok({"scriptId": new_id, "scriptNo": script_no, "name": name})


@bp.get("/media")
def media_api() -> Response:
    """四期专项 C：取回执行媒体（视频内联播放 / Trace 下载）。

    路径只允许落在 `data/exec-media` 内；越界或不存在一律 404。
    """
    conn = db()
    project_id = scope_arg()
    if project_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验媒体归属")
    relative = str(request.args.get("path") or "").strip()
    path = media_service.media_file(conn, relative)
    if path is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "媒体不存在或路径不合法")
    as_attachment = path.suffix.lower() == ".zip"
    return send_file(path, as_attachment=as_attachment, download_name=path.name)


@bp.get("/steps/<pk>/evidence")
def step_evidence_api(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    project_id = scope_arg()
    if project_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验证据归属")
    evidence = exploratory_service.step_evidence(conn, target)
    if evidence is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "该步骤没有证据快照")
    if int(evidence.get("projectId") or -1) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "证据不属于该项目")
    return ok(evidence)


@bp.post("/sessions/<pk>/steps/<step_id>/to-bug")
def step_to_bug_api(pk: str, step_id: str) -> Response:
    """异常步一键登记缺陷：有未审产物走审核转缺陷，否则直接建缺陷，证据随行挂载。"""
    conn = db()
    target = path_int(pk)
    row = ExplorationSessionRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    guard_project_scope(row, scope_arg(), "探索会话")
    result = exploratory_service.register_step_bug(conn, target, path_int(step_id), body())
    action = (audit_service.AUDIT_ACTION_UPDATE if result["alreadyLinked"]
              else audit_service.AUDIT_ACTION_CREATE)
    audit(conn, action, audit_service.RESOURCE_BUG,
          int(result["bugId"]), str(result["bugNo"]),
          {"source": "exploratory", "sessionId": target, "stepId": int(result["stepId"]),
           "alreadyLinked": result["alreadyLinked"]})
    return ok(result)
