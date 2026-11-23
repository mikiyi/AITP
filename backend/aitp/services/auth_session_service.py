"""登录态档案（四期专项 F）。

按「项目 + 环境 + 角色」保存浏览器认证态（Playwright `storageState`：cookies + localStorage），
执行前先做过期预检——有效直接复用，过期明确报「登录态已过期」，而不是执行到一半才失败。

口径：
  - **只存本机**：`data/auth-sessions/<项目>/<环境>/<角色>.json`，不进整包备份 / 资产包 / 留存归档；
  - **不自动续登录**：过期只报告，不静默重登（单用户哲学，避免静默提权）；
  - **不外发**：登录态内容不进提示词、不进 AI 请求；
  - **不阻塞**：档案缺失或过期时执行器回落正常登录路径。
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from ..common.secret_store import atomic_write_bytes
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..logging_setup import log_event

logger = logging.getLogger(__name__)

SESSION_DIR = "auth-sessions"
#: 角色名允许的字符：避免拼出越界路径
_ROLE_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _root(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    db_path = Path(str(row[2])) if row is not None and row[2] else Path("aitp.db")
    return db_path.parent / SESSION_DIR


def _safe(text: str, fallback: str) -> str:
    cleaned = "".join(ch for ch in str(text or "") if ch in _ROLE_SAFE)
    return cleaned or fallback


def relative_path(project_id: int, env_id: int | None, role: str) -> str:
    return f"{int(project_id)}/{int(env_id) if env_id else 0}/{_safe(role, 'default')}.json"


def _decode_jwt_exp(token: str) -> int | None:
    """从 JWT payload 取 exp；解不出来返回 None。"""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload + padding)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    exp = data.get("exp") if isinstance(data, dict) else None
    try:
        return int(exp) if exp is not None else None
    except (TypeError, ValueError):
        return None


def inspect_storage_state(state: dict[str, Any]) -> dict[str, Any]:
    """过期预检：返回 cookie 数、origin 数、最早过期时间与是否已过期。"""
    import time

    now = int(time.time())
    cookies = state.get("cookies") if isinstance(state, dict) else None
    origins = state.get("origins") if isinstance(state, dict) else None
    cookie_list = [item for item in (cookies or []) if isinstance(item, dict)]
    origin_list = [item for item in (origins or []) if isinstance(item, dict)]

    deadlines: list[int] = []
    for cookie in cookie_list:
        expires = cookie.get("expires")
        try:
            value = float(str(expires))
        except (TypeError, ValueError):
            continue
        # -1 表示会话 cookie（关浏览器即失效），不参与过期判定
        if value > 0:
            deadlines.append(int(value))
    for origin in origin_list:
        for item in origin.get("localStorage") or []:
            if not isinstance(item, dict):
                continue
            exp = _decode_jwt_exp(str(item.get("value") or ""))
            if exp:
                deadlines.append(int(exp))

    earliest = min(deadlines) if deadlines else 0
    expired = bool(deadlines) and earliest <= now
    return {
        "cookieCount": len(cookie_list),
        "originCount": len(origin_list),
        "earliestExpiry": earliest,
        "expired": expired,
        "checkedAt": now,
    }


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "envId": row["env_id"],
        "role": str(row["role"] or "default"),
        "originCount": int(row["origin_count"] or 0),
        "cookieCount": int(row["cookie_count"] or 0),
        "expiresAt": str(row["expires_at"] or ""),
        "expired": bool(row["expired"]),
        "note": str(row["note"] or ""),
        "lastUsedAt": str(row["last_used_at"] or ""),
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def save(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    env_id: int | None,
    role: str,
    state: dict[str, Any],
    note: str = "",
) -> dict[str, Any]:
    """保存（覆盖）一份登录态；先做结构校验与过期预检。"""
    if not isinstance(state, dict) or "cookies" not in state:
        raise AppError(ErrorCode.PARAM_ERROR, "登录态必须是 Playwright storageState JSON（含 cookies）")
    role = _safe(role, "default")
    info = inspect_storage_state(state)
    if info["cookieCount"] == 0 and info["originCount"] == 0:
        raise AppError(ErrorCode.PARAM_ERROR, "登录态里既没有 cookie 也没有 localStorage，无法复用")

    relative = relative_path(project_id, env_id, role)
    target = _root(conn) / relative
    payload = json.dumps(state, ensure_ascii=False).encode("utf-8")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, payload)
    except OSError as exc:
        log_event(logger, "auth_session_write_failed", level=logging.WARNING,
                  error=str(exc)[:200])
        raise AppError(ErrorCode.INTERNAL_ERROR, "登录态文件写入失败") from exc

    now = utcnow_iso()
    expires_at = ""
    if info["earliestExpiry"]:
        from datetime import datetime, timezone

        expires_at = datetime.fromtimestamp(info["earliestExpiry"], tz=timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM biz_auth_session WHERE project_id = ? AND COALESCE(env_id, 0) = ?"
        " AND role = ? AND is_deleted = 0",
        (int(project_id), int(env_id or 0), role),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE biz_auth_session SET file_name = ?, cookie_count = ?, origin_count = ?,"
            " expires_at = ?, expired = ?, note = ?, updated_at = ? WHERE id = ?",
            (relative, info["cookieCount"], info["originCount"], expires_at,
             1 if info["expired"] else 0, note, now, int(existing["id"])),
        )
        target_id = int(existing["id"])
    else:
        cursor = conn.execute(
            "INSERT INTO biz_auth_session (project_id, env_id, role, file_name, cookie_count,"
            " origin_count, expires_at, expired, note, last_used_at, created_at, updated_at,"
            " is_deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, 0)",
            (int(project_id), env_id, role, relative, info["cookieCount"], info["originCount"],
             expires_at, 1 if info["expired"] else 0, note, now, now),
        )
        target_id = int(cursor.lastrowid or 0)
    return get(conn, target_id, project_id=project_id)


def get(conn: sqlite3.Connection, session_id: int, *,
        project_id: int | None = None) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM biz_auth_session WHERE id = ? AND is_deleted = 0", (int(session_id),)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "登录态档案不存在")
    if project_id is not None and int(row["project_id"]) != int(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "登录态档案不属于该项目")
    return _row_payload(row)


def list_sessions(conn: sqlite3.Connection, *, project_id: int,
                  env_id: int | None = None) -> list[dict[str, Any]]:
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [int(project_id)]
    if env_id is not None:
        where.append("COALESCE(env_id, 0) = ?")
        params.append(int(env_id))
    rows = conn.execute(
        f"SELECT * FROM biz_auth_session WHERE {' AND '.join(where)} ORDER BY role",
        params,
    ).fetchall()
    return [_row_payload(row) for row in rows]


def load_state(conn: sqlite3.Connection, session_id: int, *,
               project_id: int | None = None,
               touch: bool = True) -> dict[str, Any]:
    """读取登录态并做过期预检；过期直接抛「登录态已过期」，由执行器回落正常登录。"""
    payload = get(conn, session_id, project_id=project_id)
    row = conn.execute(
        "SELECT file_name FROM biz_auth_session WHERE id = ?", (int(session_id),)
    ).fetchone()
    relative = str(row["file_name"] or "") if row is not None else ""
    target = media_safe_file(_root(conn), relative)
    if target is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "登录态文件已丢失，请重新保存")
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, "登录态文件损坏，请重新保存") from exc
    info = inspect_storage_state(state)
    if info["expired"]:
        conn.execute(
            "UPDATE biz_auth_session SET expired = 1, updated_at = ? WHERE id = ?",
            (utcnow_iso(), int(session_id)),
        )
        raise AppError(ErrorCode.ILLEGAL_OPERATION,
                       f"登录态已过期（最早到期时间 {payload['expiresAt'] or '未知'}），请重新保存")
    if touch:
        conn.execute(
            "UPDATE biz_auth_session SET last_used_at = ? WHERE id = ?",
            (utcnow_iso(), int(session_id)),
        )
    return {"session": payload, "state": state, "precheck": info}


def remove(conn: sqlite3.Connection, session_id: int, *,
           project_id: int | None = None) -> dict[str, Any]:
    payload = get(conn, session_id, project_id=project_id)
    row = conn.execute(
        "SELECT file_name FROM biz_auth_session WHERE id = ?", (int(session_id),)
    ).fetchone()
    relative = str(row["file_name"] or "") if row is not None else ""
    target = media_safe_file(_root(conn), relative)
    if target is not None:
        target.unlink(missing_ok=True)
    conn.execute(
        "UPDATE biz_auth_session SET is_deleted = 1, updated_at = ? WHERE id = ?",
        (utcnow_iso(), int(session_id)),
    )
    return {"removed": True, "session": payload}


def media_safe_file(root: Path, relative: str) -> Path | None:
    """相对路径取文件，越界返回 None（防目录穿越）。"""
    raw = str(relative or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        return None
    base = root.resolve()
    target = (base / raw).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target if target.is_file() else None
