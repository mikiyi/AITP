"""MCP 访问令牌服务：生成、轮换、停用/启用、删除与旧令牌迁移。

产品约束：
  - 同一时刻只有一个生效令牌；历史令牌保留记录，页面上可停用/启用/删除；
  - 明文只在生成/轮换响应里返回一次，数据库只存 SHA-256 哈希与末四位掩码；
  - 轮换时旧令牌立即失效，并标记为「已停用（已轮换）」；
  - 审计由 API 层统一写入，本模块只负责数据与状态流转。
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from typing import Any

from ..common.pagination import RowDict
from ..common.secret_store import get_store
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.mcp_token_repo import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    STATUS_DISABLED,
    McpTokenRepository,
)
from ..repositories.sys_config_repo import SysConfigRepository

#: 旧版本令牌在密钥库里的引用名；迁移完成后删除，避免双份真值。
LEGACY_TOKEN_REF = "mcp-token"

REASON_MANUAL = "manual"
REASON_ROTATED = "rotated"
REASON_REPLACED = "replaced"
REASON_MIGRATED = "migrated"

_TOKEN_BYTES = 24
_LIST_LIMIT = 50


def token_hash_of(raw: str) -> str:
    """令牌哈希：与 CI 令牌同口径，数据库只存这个值。"""
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def preview_of(raw: str) -> str:
    """掩码预览：只露末四位，页面与审计都用它标识令牌。"""
    return f"•••• {raw[-4:]}" if raw else ""


def _legacy_token(conn: sqlite3.Connection) -> str:
    """读取旧来源令牌：优先 sys_config（历史版本），其次本机密钥库。"""
    legacy = str(SysConfigRepository(conn).get_value("mcp.token", "") or "")
    if legacy:
        return legacy
    try:
        return str(get_store().get(LEGACY_TOKEN_REF) or "")
    except Exception:  # noqa: BLE001 - 密钥库不可用时按未配置处理
        return ""


def migrate_legacy_token(conn: sqlite3.Connection) -> None:
    """把旧令牌懒迁移进令牌表：保留可用性，并清掉旧来源与明文。

    只在令牌表完全为空时执行一次；即使旧令牌已删空，也不会重复迁移。
    """
    repo = McpTokenRepository(conn)
    if repo.list_recent(limit=1):
        return
    raw = _legacy_token(conn)
    if not raw:
        return
    now = utcnow_iso()
    repo.insert({
        "token_hash": token_hash_of(raw),
        "preview": preview_of(raw),
        "status": STATUS_ACTIVE,
        "disable_reason": REASON_MIGRATED,
        "created_at": now,
        "updated_at": now,
        "last_client": "",
    })
    SysConfigRepository(conn).set_value(
        "mcp.token", "", "string", "MCP 访问令牌（已迁移到令牌表）",
    )
    try:
        get_store().delete(LEGACY_TOKEN_REF)
    except Exception:  # noqa: BLE001 - 删除失败不影响迁移后的可用性
        logging.getLogger(__name__).debug("旧 MCP 令牌密钥库条目删除失败，已忽略", exc_info=True)
    conn.commit()


def list_tokens(conn: sqlite3.Connection) -> list[RowDict]:
    """列出全部令牌记录（含已停用/已删除），并顺带完成旧令牌迁移。"""
    migrate_legacy_token(conn)
    return McpTokenRepository(conn).list_recent(_LIST_LIMIT)


def state(conn: sqlite3.Connection) -> tuple[RowDict | None, bool]:
    """返回 ``(当前生效令牌, 是否需要令牌)``。

    只要历史上生成过令牌，就持续要求携带令牌：停用或删除最后一个令牌后
    不能让服务悄悄退回「回环免令牌」，否则停用并不等于失效。
    """
    migrate_legacy_token(conn)
    repo = McpTokenRepository(conn)
    return repo.get_active(), repo.count_all() > 0


def verify(conn: sqlite3.Connection, raw_token: str) -> RowDict | None:
    """校验明文令牌：哈希命中且状态为生效才通过。"""
    if not raw_token:
        return None
    return McpTokenRepository(conn).verify(token_hash_of(raw_token))


def touch(conn: sqlite3.Connection, token_id: int, client: str) -> None:
    """更新最后使用时间与客户端，并提交（调用留痕同款做法）。"""
    McpTokenRepository(conn).touch(token_id, utcnow_iso(), client)
    conn.commit()


def create_token(conn: sqlite3.Connection) -> tuple[str, RowDict, bool]:
    """生成新令牌；已有生效令牌时自动轮换。

    返回 ``(明文, 新令牌行, 是否轮换)``；明文只在此刻返回一次。
    """
    repo = McpTokenRepository(conn)
    now = utcnow_iso()
    previous = repo.get_active()
    if previous is not None:
        repo.update(previous["id"], {
            "status": STATUS_DISABLED,
            "disable_reason": REASON_ROTATED,
            "disabled_at": now,
        })
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    token_id = repo.insert({
        "token_hash": token_hash_of(raw),
        "preview": preview_of(raw),
        "status": STATUS_ACTIVE,
        "disable_reason": "",
        "rotated_from": int(previous["id"]) if previous is not None else None,
        "created_at": now,
        "updated_at": now,
        "last_client": "",
    })
    conn.commit()
    row = repo.get(token_id)
    if row is None:
        raise AppError(ErrorCode.INTERNAL_ERROR, "令牌写入失败")
    return raw, row, previous is not None


def disable_token(conn: sqlite3.Connection, token_id: int) -> RowDict:
    """停用生效中的令牌；历史记录保留，可再次启用。"""
    repo = McpTokenRepository(conn)
    row = repo.get(token_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "令牌不存在")
    if row.get("status") != STATUS_ACTIVE:
        raise AppError(ErrorCode.PARAM_ERROR, "只有生效中的令牌可以停用")
    repo.update(token_id, {
        "status": STATUS_DISABLED,
        "disable_reason": REASON_MANUAL,
        "disabled_at": utcnow_iso(),
    })
    conn.commit()
    return repo.get(token_id) or row


def enable_token(conn: sqlite3.Connection, token_id: int) -> RowDict:
    """重新启用已停用令牌：会自动停用当前的生效令牌，保证同时只有一个生效。"""
    repo = McpTokenRepository(conn)
    row = repo.get(token_id)
    if row is None or row.get("status") == STATUS_DELETED:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "令牌不存在或已删除")
    if row.get("status") == STATUS_ACTIVE:
        raise AppError(ErrorCode.PARAM_ERROR, "该令牌已经是生效状态")
    now = utcnow_iso()
    current = repo.get_active()
    if current is not None:
        repo.update(current["id"], {
            "status": STATUS_DISABLED,
            "disable_reason": REASON_REPLACED,
            "disabled_at": now,
        })
    repo.update(token_id, {
        "status": STATUS_ACTIVE,
        "disable_reason": "",
        "disabled_at": None,
    })
    conn.commit()
    return repo.get(token_id) or row


def delete_token(conn: sqlite3.Connection, token_id: int) -> RowDict:
    """删除令牌：状态置 deleted，记录保留用于审计，删除后不可恢复。"""
    repo = McpTokenRepository(conn)
    row = repo.get(token_id)
    if row is None or row.get("status") == STATUS_DELETED:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "令牌不存在或已删除")
    repo.update(token_id, {
        "status": STATUS_DELETED,
        "deleted_at": utcnow_iso(),
    })
    conn.commit()
    return repo.get(token_id) or row


def to_public(row: Any) -> dict[str, Any]:
    """令牌记录的前端展示字段：永远不含明文与哈希。"""
    return {
        "id": row["id"],
        "preview": row["preview"],
        "status": row["status"],
        "disableReason": row["disable_reason"],
        "createdAt": row["created_at"],
        "lastUsedAt": row["last_used_at"] or "",
        "lastClient": row["last_client"] or "",
        "disabledAt": row["disabled_at"] or "",
        "deletedAt": row["deleted_at"] or "",
    }
