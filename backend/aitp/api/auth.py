"""本地访问口令 API：状态、登录、登出、设置/修改/关闭口令。

口令与数据库同级存放（access_password.json），接口只回传启用状态与时间，
任何响应都不出现哈希或明文。会话令牌走 HttpOnly Cookie，
所以下载链接（window.open / <a href>）也自动带着登录态。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, request

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import access_service, audit_service
from .helpers import audit, body, db

bp = Blueprint("auth", __name__)


def _data_dir() -> Path:
    return Path(str(current_app.config["AITP_DB_PATH"])).parent


def _token_of() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(access_service.COOKIE_NAME, "")


def _client_key() -> str:
    return request.remote_addr or "local"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _password_of(data: dict[str, Any], field: str = "口令") -> str:
    text = str(data.get("password") or "").strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field}不能为空")
    if len(text) > access_service.MAX_PASSWORD_LENGTH:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{field}长度不能超过 {access_service.MAX_PASSWORD_LENGTH}",
        )
    return text


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        access_service.COOKIE_NAME,
        token,
        max_age=access_service.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="Strict",
        path="/api",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(access_service.COOKIE_NAME, path="/api")


def _status_payload() -> dict[str, Any]:
    data_dir = _data_dir()
    record = access_service.load_record(data_dir)
    enabled = record is not None
    return {
        "enabled": enabled,
        "authenticated": (not enabled) or access_service.is_valid(_token_of()),
        "passwordUpdatedAt": str((record or {}).get("updatedAt") or ""),
        "sessionTtlSeconds": access_service.SESSION_TTL_SECONDS,
        "lockedSeconds": access_service.locked_seconds(_client_key()) if enabled else 0,
    }


@bp.get("/status")
def status() -> Response:
    return ok(_status_payload())


@bp.post("/login")
def login() -> Response:
    data_dir = _data_dir()
    record = access_service.load_record(data_dir)
    if record is None:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "尚未启用访问口令，无需登录")
    key = _client_key()
    locked = access_service.locked_seconds(key)
    if locked:
        raise AppError(ErrorCode.AUTH_LOCKED, f"口令尝试次数过多，请 {locked} 秒后再试")
    password = _password_of(body())
    if not access_service.verify_password(str(record.get("hash") or ""), password):
        access_service.record_failure(key)
        left = access_service.remaining_attempts(key)
        message = f"口令不正确，还可尝试 {left} 次" if left > 0 else "口令不正确，请稍后再试"
        raise AppError(ErrorCode.AUTH_FAILED, message)
    access_service.reset_failures(key)
    token, expires_at = access_service.create_session()
    response = ok({
        "authenticated": True,
        "expiresAt": _iso(expires_at),
        "sessionTtlSeconds": access_service.SESSION_TTL_SECONDS,
    })
    _set_cookie(response, token)
    return response


@bp.post("/logout")
def logout() -> Response:
    access_service.revoke(_token_of())
    response = ok({"loggedOut": True})
    _clear_cookie(response)
    return response


@bp.put("/password")
def save_password() -> Response:
    """首次设置或修改口令；修改时必须带上当前口令。成功后换发新会话。"""
    data_dir = _data_dir()
    data = body()
    record = access_service.load_record(data_dir)
    if record is not None:
        current = str(data.get("currentPassword") or "").strip()
        if not current:
            raise AppError(ErrorCode.PARAM_ERROR, "请输入当前口令")
        if not access_service.verify_password(str(record.get("hash") or ""), current):
            raise AppError(ErrorCode.AUTH_FAILED, "当前口令不正确")
    password = _password_of(data, "新口令")
    confirm = str(data.get("confirmPassword") or "").strip()
    if password != confirm:
        raise AppError(ErrorCode.PARAM_ERROR, "两次输入的口令不一致")
    if len(password) < access_service.MIN_PASSWORD_LENGTH:
        raise AppError(ErrorCode.PARAM_ERROR, f"口令至少 {access_service.MIN_PASSWORD_LENGTH} 位")

    saved = access_service.configure(data_dir, password)
    token, expires_at = access_service.create_session()
    conn = db()
    audit(
        conn,
        audit_service.AUDIT_ACTION_CONFIG,
        audit_service.RESOURCE_ACCESS_GUARD,
        None,
        "本地访问口令",
        {"enabled": True},
    )
    response = ok({
        "enabled": True,
        "authenticated": True,
        "passwordUpdatedAt": str(saved.get("updatedAt") or ""),
        "expiresAt": _iso(expires_at),
        "sessionTtlSeconds": access_service.SESSION_TTL_SECONDS,
    })
    _set_cookie(response, token)
    return response


@bp.delete("/password")
def remove_password() -> Response:
    """关闭口令；必须带上当前口令，关闭后所有会话一并失效。"""
    data_dir = _data_dir()
    record = access_service.load_record(data_dir)
    if record is None:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "尚未启用访问口令")
    password = _password_of(body())
    if not access_service.verify_password(str(record.get("hash") or ""), password):
        raise AppError(ErrorCode.AUTH_FAILED, "口令不正确")
    access_service.remove(data_dir)
    conn = db()
    audit(
        conn,
        audit_service.AUDIT_ACTION_DELETE,
        audit_service.RESOURCE_ACCESS_GUARD,
        None,
        "本地访问口令",
        {"enabled": False},
    )
    response = ok({"enabled": False, "authenticated": True})
    _clear_cookie(response)
    return response
