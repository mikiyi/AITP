"""CI 令牌与通知渠道仓储。"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
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

    def revoke_all(self) -> int:
        cursor = self.conn.execute("UPDATE sys_ci_token SET enabled = 0 WHERE enabled = 1")
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
