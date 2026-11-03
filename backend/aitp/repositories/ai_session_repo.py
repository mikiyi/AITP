"""AI 会话、消息与任务仓储。"""

from __future__ import annotations

import json
from typing import Any

from ..common.pagination import RowDict, RowList
from ..common.time_utils import utcnow_iso
from .base import BaseRepository, loads


class AISessionRepository(BaseRepository):
    table = "biz_ai_session"
    default_order = "updated_at"
    order_whitelist = {"id", "title", "module", "created_at", "updated_at"}
    searchable = ["title"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["messages"] = self.list_messages(pk)
        return row

    def list_messages(self, session_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_ai_message WHERE session_id = ? AND is_deleted = 0"
            " ORDER BY id ASC",
            (session_id,),
        ).fetchall()
        messages: RowList = []
        for row in rows:
            message = dict(row)
            message["meta"] = loads(message.get("message_meta"), {}) or {}
            message.pop("message_meta", None)
            messages.append(message)
        return messages

    def add_message(self, session_id: int, role: str, content: str, **fields: Any) -> int:
        return AIMessageRepository(self.conn).add(session_id, role, content, **fields)


class AIMessageRepository(BaseRepository):
    table = "biz_ai_message"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "role", "created_at"}
    searchable = ["content"]

    def add(self, session_id: int, role: str, content: str, **fields: Any) -> int:
        payload = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": fields.pop("created_at", None),
            "updated_at": fields.pop("updated_at", None),
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        payload.update(fields)
        return self.insert(payload)


class AITaskRepository(BaseRepository):
    table = "biz_ai_task"
    default_order = "created_at"
    order_whitelist = {"id", "task_type", "status", "created_at", "updated_at"}
    searchable = ["task_type", "status"]

    def start(self, task_type: str, **fields: Any) -> int:
        payload = {
            "task_type": task_type,
            "status": "running",
            "started_at": utcnow_iso(),
            **fields,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        return self.insert(payload)

    def finish(self, pk: int, status: str, result: dict[str, Any] | None = None,
               error_code: int | None = None, error_message: str | None = None) -> bool:
        payload: dict[str, Any] = {
            "status": status,
            "finished_at": utcnow_iso(),
        }
        if result is not None:
            payload["result_json"] = json.dumps(result, ensure_ascii=False)
        if error_code is not None:
            payload["error_code"] = error_code
        if error_message is not None:
            payload["error_message"] = error_message
        return self.update(pk, payload)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["request"] = loads(row.get("request_json"), {}) or {}
        row["result"] = loads(row.get("result_json"), {}) or {}
        row.pop("request_json", None)
        row.pop("result_json", None)
        return row
