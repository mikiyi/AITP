"""CI 令牌与通知渠道仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from ..common.time_utils import utcnow_iso
from .base import BaseRepository, dumps, loads

NOTIFICATION_PENDING = "pending"
NOTIFICATION_SUCCESS = "success"
NOTIFICATION_FAILED = "failed"
NOTIFICATION_STATUSES: tuple[str, ...] = (
    NOTIFICATION_PENDING,
    NOTIFICATION_SUCCESS,
    NOTIFICATION_FAILED,
)
NOTIFICATION_STATUS_LABEL = {
    NOTIFICATION_PENDING: "待发送",
    NOTIFICATION_SUCCESS: "已送达",
    NOTIFICATION_FAILED: "发送失败",
}


class CiTokenRepository(BaseRepository):
    table = "sys_ci_token"
    soft_delete_enabled = False

    def get_active(self) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM sys_ci_token WHERE enabled = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def find_by_hash(self, token_hash: str) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM sys_ci_token WHERE token_hash = ? AND enabled = 1 LIMIT 1",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None

    def find_by_token_hash(self, token_hash: str) -> RowDict | None:
        """按哈希定位（不看启用/吊销/过期），用于诊断拒绝原因。"""
        row = self.conn.execute(
            "SELECT * FROM sys_ci_token WHERE token_hash = ? LIMIT 1",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None

    def verify(self, token_hash: str, now: str) -> RowDict | None:
        """哈希 + 启用 + 未吊销 + 未过期四重校验，只回真实可用的行。

        expires_at 为空（旧令牌迁移后默认）视为不过期；revoked_at 非空即已吊销。
        """
        row = self.conn.execute(
            "SELECT * FROM sys_ci_token"
            " WHERE token_hash = ? AND enabled = 1 AND revoked_at IS NULL"
            "   AND (expires_at IS NULL OR expires_at > ?)"
            " LIMIT 1",
            (token_hash, now),
        ).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 20) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM sys_ci_token ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_all(self, when: str | None = None) -> int:
        """一键吊销：enabled 置 0，并补记 revoked_at（已有则保留最早那次），供校验与列表展示。"""
        cursor = self.conn.execute(
            "UPDATE sys_ci_token SET enabled = 0, revoked_at = COALESCE(revoked_at, ?),"
            " updated_at = ? WHERE enabled = 1",
            (when, when),
        )
        return cursor.rowcount

    def revoke_one(self, pk: int, when: str) -> int:
        cursor = self.conn.execute(
            "UPDATE sys_ci_token SET enabled = 0, revoked_at = COALESCE(revoked_at, ?),"
            " updated_at = ? WHERE id = ? AND enabled = 1",
            (when, when, pk),
        )
        return cursor.rowcount

    def touch(self, pk: int, when: str) -> None:
        self.conn.execute("UPDATE sys_ci_token SET last_used_at = ? WHERE id = ?", (when, pk))


class NotificationChannelRepository(BaseRepository):
    table = "sys_notification_channel"
    default_order = "id"
    default_order_dir = "asc"
    searchable = ["name", "webhook_url"]

    def list_enabled(self) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM sys_notification_channel WHERE enabled = 1 AND is_deleted = 0 ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]


class NotificationLogRepository(BaseRepository):
    table = "sys_notification_log"
    soft_delete_enabled = False
    default_order = "id"
    default_order_dir = "desc"
    searchable = ["event", "title", "channel_name", "error_message"]


def channel_summary(row: dict[str, Any]) -> dict[str, Any]:
    raw_events = loads(row.get("events_json"), []) or []
    events = [str(item) for item in raw_events] if isinstance(raw_events, list) else []
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "projectId": row.get("project_id"),
        "webhookUrl": row.get("webhook_url"),
        "events": events,
        "enabled": bool(row.get("enabled")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def log_summary(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or NOTIFICATION_PENDING)
    return {
        "id": row.get("id"),
        "channelId": row.get("channel_id"),
        "channelName": row.get("channel_name"),
        "projectId": row.get("project_id"),
        "event": row.get("event"),
        "title": row.get("title"),
        "status": status,
        "statusLabel": NOTIFICATION_STATUS_LABEL.get(status, status),
        "attempts": row.get("attempts"),
        "httpStatus": row.get("http_status"),
        "responseText": row.get("response_text"),
        "errorMessage": row.get("error_message"),
        "taskId": row.get("task_id"),
        "createdAt": row.get("created_at"),
        "finishedAt": row.get("finished_at"),
    }


def events_json(events: list[str]) -> str:
    return dumps(events)


def token_status(row: dict[str, Any] | None, now: str | None = None) -> str:
    """令牌状态：active / revoked / expired / inactive，供列表与状态页展示。"""
    if not row:
        return "inactive"
    stamp = now or utcnow_iso()
    if not int(row.get("enabled") or 0) or row.get("revoked_at"):
        return "revoked"
    expires_at = row.get("expires_at")
    if expires_at and str(expires_at) <= stamp:
        return "expired"
    return "active"


def token_summary(row: dict[str, Any]) -> dict[str, Any]:
    """令牌对外摘要：只暴露展示字段，绝不回传哈希与前缀之外的敏感信息。"""
    status = token_status(row)
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "tokenPrefix": row.get("token_prefix"),
        "projectId": row.get("project_id"),
        "enabled": bool(row.get("enabled")),
        "status": status,
        "statusLabel": {"active": "有效", "revoked": "已吊销", "expired": "已过期"}.get(
            status, status
        ),
        "expiresAt": row.get("expires_at"),
        "revokedAt": row.get("revoked_at"),
        "expired": status == "expired",
        "revoked": status == "revoked",
        "lastUsedAt": row.get("last_used_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
