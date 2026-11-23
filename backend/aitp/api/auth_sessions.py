"""登录态档案 API（四期专项 F）。

列表 / 保存（导入 storageState）/ 查看预检 / 清除。所有读写按 projectId 收敛。
登录态内容只落本机，读取时返回结构摘要而非 cookie 明文（防误拷贝外发）。
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import optional_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, auth_session_service
from .helpers import body, commit, db, path_int, require_project, scope_arg

bp = Blueprint("auth_sessions", __name__)

RESOURCE = "auth_session"


def _project_id() -> int:
    raw = scope_arg()
    if raw is None:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验登录态归属")
    return int(raw)


@bp.get("")
def list_sessions_api() -> Response:
    conn = db()
    project_id = _project_id()
    require_project(conn, project_id)
    env_id = optional_int(request.args.get("envId"), "envId")
    return ok({
        "list": auth_session_service.list_sessions(
            conn, project_id=project_id, env_id=env_id,
        ),
        "projectId": project_id,
        "envId": env_id,
    })


@bp.post("")
def save_session_api() -> Response:
    """导入一份 Playwright storageState JSON 作为登录态档案。"""
    conn = db()
    project_id = _project_id()
    require_project(conn, project_id)
    data = body()
    env_id = optional_int(data.get("envId"), "envId")
    role = str(data.get("role") or "default").strip() or "default"
    state = data.get("storageState")
    note = str(data.get("note") or "").strip()
    result = auth_session_service.save(
        conn, project_id=project_id, env_id=env_id, role=role,
        state=state if isinstance(state, dict) else {},
        note=note,
    )
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_UPDATE, RESOURCE, int(result["id"]),
        f"{project_id}/{env_id or 0}/{role}", {"cookieCount": result["cookieCount"]},
    )
    commit(conn)
    return ok(result)


@bp.get("/<pk>")
def get_session_api(pk: str) -> Response:
    """查看档案概要 + 过期预检结果（不返回 cookie / localStorage 明文）。"""
    conn = db()
    project_id = _project_id()
    target = path_int(pk)
    payload = auth_session_service.get(conn, target, project_id=project_id)
    precheck = None
    row = conn.execute(
        "SELECT file_name FROM biz_auth_session WHERE id = ?", (target,)
    ).fetchone()
    path = auth_session_service.media_safe_file(
        auth_session_service._root(conn), str(row["file_name"] or "") if row else "",
    )
    if path is not None:
        import json

        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            precheck = auth_session_service.inspect_storage_state(state)
        except (OSError, ValueError):
            precheck = None
    return ok({"session": payload, "precheck": precheck})


@bp.post("/<pk>/precheck")
def precheck_session_api(pk: str) -> Response:
    """执行前预检：可用返回 confirm=true，过期明确报错（不静默重登）。"""
    conn = db()
    project_id = _project_id()
    result = auth_session_service.load_state(conn, path_int(pk), project_id=project_id)
    commit(conn)
    return ok({
        "confirm": True,
        "session": result["session"],
        "precheck": result["precheck"],
    })


@bp.delete("/<pk>")
def remove_session_api(pk: str) -> Response:
    conn = db()
    project_id = _project_id()
    result = auth_session_service.remove(conn, path_int(pk), project_id=project_id)
    audit_service.record(
        conn, audit_service.AUDIT_ACTION_DELETE, RESOURCE, path_int(pk),
        str(result["session"]["role"]), {"projectId": project_id},
    )
    commit(conn)
    return ok(result)
